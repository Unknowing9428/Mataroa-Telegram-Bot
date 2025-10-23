import json
import os
import logging
import re
import time
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Tuple, Set
from enum import IntEnum
import secrets

import httpx
import aiofiles
from json import JSONDecodeError
from urllib.parse import urlparse
from pathlib import Path
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ChatAction
from telegram.helpers import escape_markdown
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.WARNING
)
logger = logging.getLogger(__name__)


# ---------- Constants ----------
DEFAULT_PREVIEW_LENGTH = 280
POSTS_PAGE_SIZE = 5
POSTS_CACHE_TTL = 10.0
HTTP_TIMEOUT = 12.0
MAX_PREVIEW_CHARS = 3900  # safe cap below Telegram 4096 limit

DRAFT_COOLDOWN_SEC = 1.5
DELETE_GRACE_SEC = 15

ALLOWED_PREVIEW_LENGTHS = {140, 280, 500}
SLUG_RE = re.compile(r"^[a-z0-9-]{1,128}$")

TEMPLATES = {
    "outline": "# Outline\n- Intro\n- Body\n- Conclusion\n",
    "notes": "# Notes\n- Point 1\n- Point 2\n",
    "links": "# Links\n- [Title](https://example.com) - note\n",
}

# ---------- Messages ----------
MESSAGES: Dict[str, str] = {
    # Generic/system
    "PLEASE_WAIT": "⏳ Please wait...",
    "ACCESS_DENIED": "Access denied.",
    "NEED_API_KEY": "🔑 Set your API key first using /start.",
    "INVALID_ACTION": "Invalid action.",
    "INVALID_OR_STALE_ACTION": "Invalid or stale action.",
    "OP_CANCELLED": "Operation cancelled.",
    "PRIVACY_DM": "For privacy, please use me in a private chat.",
    "SET_API_PRIVATE": "For security, please message me in a private chat to set your API key.",

    # Start/Help/Status
    "START_WELCOME": "👋 Welcome to the Mataroa.blog bot! Please enter your API key.",
    "API_SAVED": "✅ API key saved! Use /new, /update, /delete, /list, /drafts, /published, /search, /settings or /help.",
    "HELP_TEXT": (
        "🤖 Mataroa.blog Bot Help\n\n"
        "/start - Set your API key\n"
        "/new or /post - Create a new post (multi-message drafting: /done, /preview, /clear, /cancel)\n"
        "/update - Update an existing post\n"
        "/delete - Delete a post\n"
        "/list - List your posts with filters & pagination\n"
        "/drafts - List drafts only\n"
        "/published - List published only\n"
        "/search <query> - Search in titles and bodies\n"
        "/settings - Adjust default publish mode, preview length, confirm-delete\n"
        "/status - Check API connectivity\n"
    ),
    "STATUS_API_REACHABLE": "✅ API reachable. {ms} ms",
    "STATUS_API_UNREACHABLE": "❌ API unreachable or unauthorized.",

    # Draft/new post flow
    "RESUME_DRAFT": "📝 Resuming draft: {title}\nSend message chunks to append. Use /done when finished.",
    "ENTER_TITLE_PROMPT": "📝 Enter the title of your post:",
    "NOW_SEND_BODY": "✏️ Now send the body in multiple messages. Use /done when finished.",
    "SEND_BODY_MULTIMSG": "✏️ Send the body/content in multiple messages. Use /done when finished.\nCommands: /preview /clear /cancel",
    "PROMPT_VALID_TITLE": "Please provide a valid title:",
    "PROMPT_VALID_CONTENT": "Please provide valid content:",
    "ADDED_TO_DRAFT": "✅ Added to draft. Use /preview or /done.",
    "DRAFT_CLEARED": "🧹 Draft cleared.",
    "REMOVED_LAST_CHUNK": "↩️ Removed last chunk.",
    "NOTHING_TO_UNDO": "Nothing to undo.",
    "DRAFT_EMPTY": "Draft is empty. Add content first.",
    "CHOOSE_PUBLICATION_OPTION": "Choose publication option:",

    # Update flow
    "ENTER_UPDATE_SLUG_PROMPT": "✏️ Enter the slug of the post to update:",
    "FAILED_FETCH_POST_DETAILS": "❌ Failed to fetch post details.",
    "FAILED_FETCH_POST_DETAILS_CHECK": "❌ Failed to fetch post details. Check the slug and try again.",
    "CURRENT_TITLE_LABEL": "Current Title:",
    "CURRENT_BODY_LABEL": "Current Body:",
    "ENTER_UPDATED_TITLE": "Enter the updated title:",
    "ENTER_UPDATED_BODY": "Enter the updated body/content:",
    "SLUG_SUGGESTION_NOTE_MD": "\n\nSlug suggestion: {current} → {suggested} \(toggle before submit\)",
    "SLUG_SUGGESTION_UNAVAILABLE": "Slug suggestion unavailable. Adjust title.",

    # Create/update results
    "POST_CREATED": "✅ Post created.",
    "POST_CREATED_WITH_DETAILS": "✅ Post created!\nSlug: {slug}\nStatus: {status}\nURL: {url}",
    "FAILED_CREATE_POST": "❌ Failed to create post.",
    "POST_UPDATED_WITH_DETAILS": "✅ Post updated!\nSlug: {slug}\nURL: {url}",
    "FAILED_UPDATE_POST": "❌ Failed to update post.",

    # Delete flow
    "PROMPT_VALID_SLUG": "Please enter a valid slug:",
    "INVALID_SLUG": "❌ Invalid slug.",
    "CONFIRM_DELETE": "⚠️ Are you sure you want to delete post '{slug}'?",
    "DELETING_IN": "🗑️ Deleting '{slug}' in {seconds}s...",
    "POST_DELETED": "✅ Post with slug '{slug}' deleted.",
    "FAILED_DELETE_POST_FMT": "❌ Failed to delete '{slug}'.",
    "FAILED_DELETE_POST_SIMPLE": "❌ Failed to delete post.",
    "UNDO_DELETE_CANCELLED": "❎ Deletion of '{slug}' cancelled.",
    "TOO_LATE_UNDO": "Too late to undo, or no pending deletion.",

    # List/search
    "FAILED_FETCH_POSTS": "❌ Failed to fetch posts. Try again later.",
    "LIST_HEADER": "📄 Your Posts ({total}):",
    "LIST_NO_MATCH": "(No posts match your criteria.)",
    "USAGE_SEARCH": "Usage: /search <query>",

    # Toggle publish
    "FAILED_FETCH_FOR_TOGGLE": "❌ Failed to fetch post for toggling.",
    "TOGGLED_PUBLISH_STATE": "✅ Toggled publish state.",
    "FAILED_TOGGLE_PUBLISH": "❌ Failed to toggle publish.",

    # Retry
    "NO_ACTION_TO_RETRY": "No action to retry.",
    "UNSUPPORTED_RETRY": "Unsupported retry.",

    # Buttons/labels
    "BUTTON_CANCEL": "Cancel",
    "BUTTON_DONE": "Done",
    "BUTTON_PREVIEW": "Preview",
    "BUTTON_CLEAR": "Clear",
    "BUTTON_UNDO_LAST": "Undo last",
    "BUTTON_TEMPLATE_OUTLINE": "Template: Outline",
    "BUTTON_TEMPLATE_NOTES": "Template: Notes",
    "BUTTON_TEMPLATE_LINKS": "Template: Links",
    "BUTTON_SAVE_DRAFT": "Save as Draft",
    "BUTTON_PUBLISH_NOW": "Publish Now",
    "BUTTON_SUBMIT": "Submit",
    "BUTTON_TOGGLE_SLUGSYNC": "Toggle Slug Sync",
    "BUTTON_EDIT": "Edit",
    "BUTTON_DELETE": "Delete",
    "BUTTON_UNDO_DELETE": "Undo delete",
    "BUTTON_OPEN": "Open",
    "BUTTON_SHARE": "Share",
    "BUTTON_PREV": "◀️ Prev",
    "BUTTON_NEXT": "Next ▶️",
    "BUTTON_REFRESH": "Refresh",
    "BUTTON_YES_DELETE": "Yes, Delete",
    "BUTTON_RETRY": "Retry",
    "FILTER_ALL": "All",
    "FILTER_PUBLISHED": "Published",
    "FILTER_DRAFTS": "Drafts",
    "BUTTON_PAGE_X_OF_Y": "Page {page}/{total}",
    # Settings additions
    "SETTINGS_HEADER": "⚙️ Settings",
    "BUTTON_TOGGLE_DEFAULT_MODE": "Toggle Default Publish Mode",
    "BUTTON_TOGGLE_CONFIRM_DELETE": "Toggle Confirm Before Delete",
}

# ---------- Callback Data Tokens ----------
CB_CANCEL = "cancel"
CB_DRAFT_DONE = "draft:done"
CB_DRAFT_PREVIEW = "draft:preview"
CB_DRAFT_CLEAR = "draft:clear"
CB_DRAFT_UNDO = "draft:undo"
CB_TMPL_PREFIX = "tmpl:"
CB_CHOICE_DRAFT = "draft"
CB_CHOICE_PUBLISH = "publish"
CB_SUBMIT_POST = "submit_post"
CB_SUBMIT_UPDATE = "submit_update"
CB_SLUGSYNC = "slugsync"
CB_EDIT_PREFIX = "edit:"
CB_DELETE_PREFIX = "delete:"
CB_TOGGLEPUB_PREFIX = "togglepub:"
CB_LIST_PREFIX = "list:"
CB_LIST_FILTER_PREFIX = "list:filter:"
CB_LIST_PAGE_PREFIX = "list:page:"
CB_LIST_NOP = "list:nop"
CB_LIST_REFRESH = "list:refresh"
CB_CONFIRM_DELETE = "confirm_delete"
CB_UNDO_DELETE_PREFIX = "undodel:"
CB_RETRY_PREFIX = "retry:"
CB_RETRY_CREATE = "retry:create"
CB_RETRY_UPDATE = "retry:update"
CB_RETRY_DELETE = "retry:delete"
CB_RETRY_TOGGLEPUB = "retry:togglepub"
CB_SETTINGS_PREFIX = "settings:"
CB_SETTINGS_MODE = "settings:mode"
CB_SETTINGS_PREV_PREFIX = "settings:prev:"
CB_SETTINGS_CONFIRM = "settings:confirm"

# ---------- Context Keys ----------
K_TITLE = "title"
K_BODY = "body"
K_BODY_PARTS = "body_parts"
K_UNDO_STACK = "undo_stack"
K_LIST_STATE = "list_state"
K_SLUG_SYNC = "slug_sync"
K_FINAL_SLUG = "final_slug"
K_PUBLISHED_AT = "published_at"
K_CURRENT_SLUG = "current_slug"
K_SLUG = "slug"
K_CURRENT_TITLE = "current_title"
K_CURRENT_BODY = "current_body"
K_SLUG_SUGGESTED = "slug_suggested"
K_SLUG_SUGGESTED_VALID = "slug_suggested_valid"
K_POSTS_CACHE = "posts_cache"


# ---------- Conversation States ----------
class ConvState(IntEnum):
    ENTER_API_KEY = 0
    ENTER_TITLE = 1
    ENTER_BODY = 2
    ENTER_PUBLISH_CHOICE = 3
    ENTER_DELETE_SLUG = 4
    ENTER_UPDATE_SLUG = 5
    ENTER_UPDATED_TITLE = 6
    ENTER_UPDATED_BODY = 7
    ENTER_PUBLISH_CHOICE_UPDATE = 8
    CONFIRM_POST = 9
    CONFIRM_UPDATE = 10
    CONFIRM_DELETE = 11


# Keep original state names for minimal code changes
ENTER_API_KEY = ConvState.ENTER_API_KEY
ENTER_TITLE = ConvState.ENTER_TITLE
ENTER_BODY = ConvState.ENTER_BODY
ENTER_PUBLISH_CHOICE = ConvState.ENTER_PUBLISH_CHOICE
ENTER_DELETE_SLUG = ConvState.ENTER_DELETE_SLUG
ENTER_UPDATE_SLUG = ConvState.ENTER_UPDATE_SLUG
ENTER_UPDATED_TITLE = ConvState.ENTER_UPDATED_TITLE
ENTER_UPDATED_BODY = ConvState.ENTER_UPDATED_BODY
ENTER_PUBLISH_CHOICE_UPDATE = ConvState.ENTER_PUBLISH_CHOICE_UPDATE
CONFIRM_POST = ConvState.CONFIRM_POST
CONFIRM_UPDATE = ConvState.CONFIRM_UPDATE
CONFIRM_DELETE = ConvState.CONFIRM_DELETE


# API and bot settings
API_URL = "https://mataroa.blog/api/posts/"

# Secure config dir and users.json path
CONFIG_DIR = os.getenv(
    "MATAROA_BOT_DIR", os.path.join(os.path.expanduser("~"), ".config", "mataroa-bot")
)
USERS_JSON_PATH = os.path.join(CONFIG_DIR, "users.json")

users_data: Dict[int, "UserData"] = {}  # user_id -> UserData

# Allowlist from environment (comma-separated user IDs) - kept separate from file-based allowlist
ENV_ALLOWLIST_IDS: Set[int] = set(
    int(x)
    for x in re.split(r"[\,\s]+", os.getenv("ALLOWLIST_IDS", os.getenv("ALLOWLIST", "")).strip())
    if x.isdigit()
)
FILE_ALLOWLIST_IDS: Set[int] = set()


@dataclass
class UserData:
    api_key: str
    title: str = ""
    body: str = ""
    published_at: Optional[str] = None
    draft_title: str = ""
    draft_parts: List[str] = field(default_factory=list)
    undo_stack: List[str] = field(default_factory=list)
    last_action: Dict[str, Any] = field(default_factory=dict)
    settings: Dict[str, Any] = field(
        default_factory=lambda: {
            "default_publish_mode": "draft",  # draft|publish
            "preview_length": DEFAULT_PREVIEW_LENGTH,
            "confirm_before_delete": True,
        }
    )


# ---------- Helper: Inline Keyboards ----------

def cancel_keyboard() -> InlineKeyboardMarkup:
    """Build a Cancel inline keyboard."""
    return InlineKeyboardMarkup([[InlineKeyboardButton(MESSAGES["BUTTON_CANCEL"], callback_data=CB_CANCEL)]])


def drafting_keyboard() -> InlineKeyboardMarkup:
    """Build the drafting controls keyboard (done/preview/clear/undo/templates)."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(MESSAGES["BUTTON_DONE"], callback_data=CB_DRAFT_DONE),
                InlineKeyboardButton(MESSAGES["BUTTON_PREVIEW"], callback_data=CB_DRAFT_PREVIEW),
                InlineKeyboardButton(MESSAGES["BUTTON_CLEAR"], callback_data=CB_DRAFT_CLEAR),
            ],
            [
                InlineKeyboardButton(MESSAGES["BUTTON_UNDO_LAST"], callback_data=CB_DRAFT_UNDO),
            ],
            [
                InlineKeyboardButton(MESSAGES["BUTTON_TEMPLATE_OUTLINE"], callback_data=f"{CB_TMPL_PREFIX}outline"),
                InlineKeyboardButton(MESSAGES["BUTTON_TEMPLATE_NOTES"], callback_data=f"{CB_TMPL_PREFIX}notes"),
            ],
            [
                InlineKeyboardButton(MESSAGES["BUTTON_TEMPLATE_LINKS"], callback_data=f"{CB_TMPL_PREFIX}links"),
                InlineKeyboardButton(MESSAGES["BUTTON_CANCEL"], callback_data=CB_CANCEL),
            ],
        ]
    )


def publish_choice_keyboard(default_mode: Optional[str] = None) -> InlineKeyboardMarkup:
    """Build keyboard to choose draft vs publish (with default marked)."""
    draft_label = MESSAGES["BUTTON_SAVE_DRAFT"]
    publish_label = MESSAGES["BUTTON_PUBLISH_NOW"]
    if default_mode == "draft":
        draft_label += " ✓"
    elif default_mode == "publish":
        publish_label += " ✓"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(draft_label, callback_data=CB_CHOICE_DRAFT),
                InlineKeyboardButton(publish_label, callback_data=CB_CHOICE_PUBLISH),
            ],
            [InlineKeyboardButton(MESSAGES["BUTTON_CANCEL"], callback_data=CB_CANCEL)],
        ]
    )


def preview_submit_keyboard(include_slug_sync: bool = False) -> InlineKeyboardMarkup:
    """Build keyboard to submit a new post (optionally include slug sync toggle)."""
    row = [InlineKeyboardButton(MESSAGES["BUTTON_SUBMIT"], callback_data=CB_SUBMIT_POST)]
    if include_slug_sync:
        row.append(InlineKeyboardButton(MESSAGES["BUTTON_TOGGLE_SLUGSYNC"], callback_data=CB_SLUGSYNC))
    return InlineKeyboardMarkup([row, [InlineKeyboardButton(MESSAGES["BUTTON_CANCEL"], callback_data=CB_CANCEL)]])


def update_preview_submit_keyboard(include_slug_sync: bool = True) -> InlineKeyboardMarkup:
    """Build keyboard to submit an updated post (optionally include slug sync toggle)."""
    row1 = [InlineKeyboardButton(MESSAGES["BUTTON_SUBMIT"], callback_data=CB_SUBMIT_UPDATE)]
    if include_slug_sync:
        row1.append(InlineKeyboardButton(MESSAGES["BUTTON_TOGGLE_SLUGSYNC"], callback_data=CB_SLUGSYNC))
    return InlineKeyboardMarkup([row1, [InlineKeyboardButton(MESSAGES["BUTTON_CANCEL"], callback_data=CB_CANCEL)]])


# ---------- Utilities ----------

def get_user_id(update: Update) -> Optional[int]:
    """Return the effective user id from update if available."""
    if update.effective_user:
        return update.effective_user.id
    return None


def reload_env_allowlist() -> None:
    """Reload allowlist user IDs from environment at runtime."""
    global ENV_ALLOWLIST_IDS
    try:
        raw = os.getenv("ALLOWLIST_IDS", os.getenv("ALLOWLIST", "")) or ""
        raw = raw.strip()
        ids: Set[int] = set()
        if raw:
            for x in re.split(r"[\,\s]+", raw):
                if x.isdigit():
                    try:
                        ids.add(int(x))
                    except Exception:
                        pass
        ENV_ALLOWLIST_IDS = ids
    except Exception:
        ENV_ALLOWLIST_IDS = set()


def effective_allowlist() -> Set[int]:
    """Return the union of env and file-based allowlists."""
    return set(ENV_ALLOWLIST_IDS) | set(FILE_ALLOWLIST_IDS)


async def ensure_allowed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if the user is in the effective allowlist; inform user if blocked."""
    user_id = get_user_id(update)
    if not user_id:
        return False
    eff = effective_allowlist()
    if eff and user_id not in eff:
        if update.callback_query:
            try:
                await update.callback_query.answer(MESSAGES["ACCESS_DENIED"], show_alert=True)
                await update.callback_query.edit_message_text(MESSAGES["ACCESS_DENIED"])
            except Exception:
                pass
        elif update.message:
            await update.message.reply_text(MESSAGES["ACCESS_DENIED"])
        return False
    return True


def slugify(title: str) -> str:
    s = title.strip().lower()
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"[\s_-]+", "-", s)
    s = re.sub(r"^-+|-+$", "", s)
    return s


def is_valid_slug(slug: str) -> bool:
    return bool(SLUG_RE.match(slug))


def truncate(text: str, length: int) -> str:
    if len(text) <= length:
        return text
    return text[: max(0, length - 1)] + "…"


def now_date_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def cooldown_ok(
    context: ContextTypes.DEFAULT_TYPE, key: str = "tap", threshold: float = DRAFT_COOLDOWN_SEC
) -> bool:
    """Return True if monotonic cooldown window has elapsed for the provided key."""
    now = time.monotonic()
    last = context.user_data.get(f"last_{key}", 0.0)
    if now - last < threshold:
        return False
    context.user_data[f"last_{key}"] = now
    return True


async def send_typing(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    """Send a typing indicator; ignore failures."""
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    except Exception:
        pass


def is_valid_absolute_url(url: str) -> bool:
    try:
        u = urlparse(url)
        return u.scheme in ("http", "https") and bool(u.netloc)
    except Exception:
        return False


def mdv2(s: str) -> str:
    return escape_markdown(s or "", version=2)


def safe_truncate_md(s: str, max_len: int) -> str:
    """Truncate a MarkdownV2 string to max_len, avoiding dangling escape backslashes."""
    if max_len <= 0:
        return ""
    if max_len == 1:
        return "…"
    if len(s) <= max_len:
        return s
    cut = max_len - 1
    while cut > 0 and s[cut - 1] == "\\":
        cut -= 1
    return s[:cut] + "…"


def get_effective_preview_length(user_id: int) -> int:
    """Return user's preview length if valid else DEFAULT_PREVIEW_LENGTH."""
    val = users_data.get(user_id, UserData(api_key="")).settings.get(
        "preview_length", DEFAULT_PREVIEW_LENGTH
    )
    if isinstance(val, int) and val in ALLOWED_PREVIEW_LENGTHS:
        return val
    return DEFAULT_PREVIEW_LENGTH


async def ensure_api_key_or_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Optional[int]:
    """Ensure the user has an API key; prompt in message or alert in callback if missing."""
    uid = get_user_id(update)
    if uid is None:
        return None
    u = users_data.get(uid)
    if not u or not (u.api_key or "").strip():
        if update.callback_query:
            try:
                await update.callback_query.answer(
                    MESSAGES["NEED_API_KEY"], show_alert=True
                )
            except Exception:
                pass
        elif update.message:
            await update.message.reply_text(MESSAGES["NEED_API_KEY"])
        return None
    return uid


def safe_chat_id(update: Optional[Update] = None, query: Optional[Any] = None) -> Optional[int]:
    """Return chat_id from Update or CallbackQuery if available."""
    if query is not None and getattr(query, "message", None) is not None:
        return query.message.chat_id
    if update is not None:
        if getattr(update, "message", None) is not None:
            return update.message.chat_id
        if getattr(update, "effective_chat", None) is not None and update.effective_chat is not None:
            return update.effective_chat.id
    return None


def render_create_preview(title: str, body: str, published_at: Optional[str]) -> str:
    """Render MarkdownV2 preview for new post creation under MAX_PREVIEW_CHARS cap."""
    status = "Draft" if published_at is None else "Published"
    title_s = mdv2(title)
    body_s = mdv2(body)
    header_pre = "*Preview Post:*\n\n" + "*Title:*\n"
    header_post = "\n\n*Body:*\n"
    footer = "\n\n*Status:*\n" + mdv2(status)

    # First try to fit body; if overflow, shrink title; ensure total under MAX_PREVIEW_CHARS
    body_allowed = MAX_PREVIEW_CHARS - len(header_pre + title_s + header_post + body_s + footer)
    if body_allowed < 0:
        # Try shrinking title first (set body to zero for budget calc)
        title_allowed = MAX_PREVIEW_CHARS - len(header_pre + header_post + footer)
        title_allowed = max(0, title_allowed)
        if len(title_s) > title_allowed:
            title_s = safe_truncate_md(title_s, title_allowed)
        # Recompute body allowance after title truncation
        body_allowed = MAX_PREVIEW_CHARS - len(header_pre + title_s + header_post + footer)
        body_allowed = max(0, body_allowed)
        body_s = safe_truncate_md(body_s, body_allowed)
    else:
        # Ensure body within allowed maximum
        max_body = MAX_PREVIEW_CHARS - len(header_pre + title_s + header_post + footer)
        if max_body < len(body_s):
            body_s = safe_truncate_md(body_s, max(0, max_body))

    return header_pre + title_s + header_post + body_s + footer


def render_update_preview(
    title: str,
    body: str,
    published_at: Optional[str],
    current_slug: str,
    suggested: str,
    slug_sync: bool,
) -> str:
    """Render MarkdownV2 preview for post update, including slug change indication."""
    status = "Draft" if published_at is None else "Published"
    title_s = mdv2(title)
    body_s = mdv2(body)
    slug_line = mdv2(current_slug)
    if current_slug != suggested:
        slug_line = f"{mdv2(current_slug)} → {mdv2(suggested)}"
    sync_state = "sync ON" if slug_sync else "sync OFF"

    header_pre = "*Preview Updated Post:*\n\n" + "*Title:*\n"
    header_post = "\n\n*Body:*\n"
    tail_pre = "\n\n*Slug:*\n"
    tail_mid = f" \\({mdv2(sync_state)}\\)"
    tail_post = "\n\n*Status:*\n" + mdv2(status)

    # Attempt to fit content under MAX_PREVIEW_CHARS; shrink body first, then title, then slug line
    total_len = len(
        header_pre + title_s + header_post + body_s + tail_pre + slug_line + tail_mid + tail_post
    )
    if total_len > MAX_PREVIEW_CHARS:
        # 1) Shrink body
        body_allowed = MAX_PREVIEW_CHARS - len(
            header_pre + title_s + header_post + tail_pre + slug_line + tail_mid + tail_post
        )
        if body_allowed < 0:
            body_s = ""
        else:
            body_s = safe_truncate_md(body_s, body_allowed)
        # 2) Re-evaluate; shrink title if needed
        total_len = len(
            header_pre + title_s + header_post + body_s + tail_pre + slug_line + tail_mid + tail_post
        )
        if total_len > MAX_PREVIEW_CHARS:
            title_allowed = MAX_PREVIEW_CHARS - len(
                header_pre + header_post + body_s + tail_pre + slug_line + tail_mid + tail_post
            )
            title_allowed = max(0, title_allowed)
            if len(title_s) > title_allowed:
                title_s = safe_truncate_md(title_s, title_allowed)
        # 3) Re-evaluate; shrink slug line if still needed
        total_len = len(
            header_pre + title_s + header_post + body_s + tail_pre + slug_line + tail_mid + tail_post
        )
        if total_len > MAX_PREVIEW_CHARS:
            slug_allowed = MAX_PREVIEW_CHARS - len(
                header_pre + title_s + header_post + body_s + tail_pre + tail_mid + tail_post
            )
            slug_allowed = max(0, slug_allowed)
            if len(slug_line) > slug_allowed:
                slug_line = safe_truncate_md(slug_line, slug_allowed)

    return header_pre + title_s + header_post + body_s + tail_pre + slug_line + tail_mid + tail_post


# ---------- Secure Config Helpers ----------
async def ensure_config_dir() -> None:
    """Ensure the config directory exists with secure permissions where possible."""
    try:
        Path(CONFIG_DIR).mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(CONFIG_DIR, 0o700)
        except Exception:
            pass
    except Exception as e:
        logger.error("Failed to ensure config dir: %s", e)


def enforce_config_permissions() -> None:
    """Enforce secure permissions on config dir and users.json (raise on fatal errors)."""
    if os.name != "posix":
        try:
            Path(CONFIG_DIR).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error("Failed to create config dir: %s", e)
        logger.warning(
            "Skipping strict permission enforcement on non-POSIX platform: %s", os.name
        )
        return
    try:
        Path(CONFIG_DIR).mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.error("Failed to create config dir: %s", e)
        raise RuntimeError("Failed to create config directory") from e
    try:
        st = os.stat(CONFIG_DIR)
        mode = st.st_mode & 0o777
        if mode != 0o700:
            try:
                os.chmod(CONFIG_DIR, 0o700)
            except Exception:
                pass
            st2 = os.stat(CONFIG_DIR)
            if (st2.st_mode & 0o777) != 0o700:
                logger.error("Insecure permissions on %s (expected 0700).", CONFIG_DIR)
                raise RuntimeError("Insecure config dir permissions")
    except Exception as e:
        logger.error("Permission check failed for %s: %s", CONFIG_DIR, e)
        raise RuntimeError("Config dir permission check failed") from e
    if os.path.exists(USERS_JSON_PATH):
        try:
            stf = os.stat(USERS_JSON_PATH)
            fmode = stf.st_mode & 0o777
            if fmode != 0o600:
                try:
                    os.chmod(USERS_JSON_PATH, 0o600)
                except Exception:
                    pass
                stf2 = os.stat(USERS_JSON_PATH)
                if (stf2.st_mode & 0o777) != 0o600:
                    logger.error(
                        "Insecure permissions on %s (expected 0600).", USERS_JSON_PATH
                    )
                    raise RuntimeError("Insecure users.json permissions")
        except Exception as e:
            logger.error("Permission check failed for %s: %s", USERS_JSON_PATH, e)
            raise RuntimeError("users.json permission check failed") from e


# ---------- Async File I/O Helpers ----------
async def load_users_data() -> None:
    """Load users.json into memory; tolerate empty/malformed files and warn on parse errors."""
    global users_data, FILE_ALLOWLIST_IDS
    await ensure_config_dir()
    if os.path.exists(USERS_JSON_PATH):
        try:
            async with aiofiles.open(USERS_JSON_PATH, "r") as f:
                data = await f.read()
        except OSError as e:
            logger.warning("Failed to read %s: %s", USERS_JSON_PATH, e)
            users_data = {}
            FILE_ALLOWLIST_IDS = set()
            return
        if not data.strip():
            users_data = {}
            FILE_ALLOWLIST_IDS = set()
            return
        try:
            json_data = json.loads(data)
        except JSONDecodeError as e:
            logger.warning("Failed to parse %s: %s", USERS_JSON_PATH, e)
            users_data = {}
            FILE_ALLOWLIST_IDS = set()
            return
        # Support both legacy and structured format
        if isinstance(json_data, dict) and "users" in json_data:
            users_json = json_data.get("users", {})
            allowlist_json = json_data.get("allowlist", [])
            file_ids: Set[int] = set()
            for x in allowlist_json if isinstance(allowlist_json, list) else []:
                if isinstance(x, int):
                    file_ids.add(x)
                elif isinstance(x, str) and x.isdigit():
                    try:
                        file_ids.add(int(x))
                    except ValueError:
                        continue
            FILE_ALLOWLIST_IDS = file_ids
        else:
            users_json = json_data if isinstance(json_data, dict) else {}
            FILE_ALLOWLIST_IDS = set()

        parsed: Dict[int, UserData] = {}
        for k, v in users_json.items():
            try:
                uid = int(k)
            except (TypeError, ValueError):
                continue
            if isinstance(v, str):
                parsed[uid] = UserData(api_key=v)
            elif isinstance(v, dict):
                settings = v.get("settings") if isinstance(v.get("settings"), dict) else {}
                preview_len = settings.get("preview_length", DEFAULT_PREVIEW_LENGTH)
                if not isinstance(preview_len, int) or preview_len not in ALLOWED_PREVIEW_LENGTHS:
                    preview_len = DEFAULT_PREVIEW_LENGTH
                parsed[uid] = UserData(
                    api_key=str(v.get("api_key", "")),
                    title=str(v.get("title", "")),
                    body=str(v.get("body", "")),
                    published_at=v.get("published_at", None),
                    draft_title=str(v.get("draft_title", "")),
                    draft_parts=v.get("draft_parts", []) or [],
                    undo_stack=v.get("undo_stack", []) or [],
                    last_action=v.get("last_action", {}) or {},
                    settings={
                        "default_publish_mode": settings.get("default_publish_mode", "draft")
                        if isinstance(settings, dict)
                        else "draft",
                        "preview_length": preview_len,
                        "confirm_before_delete": bool(settings.get("confirm_before_delete", True))
                        if isinstance(settings, dict)
                        else True,
                    },
                )
            else:
                parsed[uid] = UserData(api_key="")
        users_data = parsed
    else:
        users_data = {}
        FILE_ALLOWLIST_IDS = set()


async def save_users_data() -> None:
    await ensure_config_dir()
    # Preserve only file-based allowlist and adopt structured schema
    to_write = {
        "users": {str(k): v.__dict__ for k, v in users_data.items()},
        "allowlist": sorted(list(FILE_ALLOWLIST_IDS)),
    }
    tmp_path = USERS_JSON_PATH + ".tmp"
    async with aiofiles.open(tmp_path, "w") as f:
        await f.write(json.dumps(to_write, indent=2, sort_keys=True))
    try:
        os.chmod(tmp_path, 0o600)
    except Exception:
        pass
    os.replace(tmp_path, USERS_JSON_PATH)
    try:
        os.chmod(USERS_JSON_PATH, 0o600)
    except Exception:
        pass


# ---------- API Helper ----------
async def api_call(
    method: str,
    api_key: str,
    slug: Optional[str] = None,
    payload: Optional[dict] = None,
    *,
    retry: bool = True,
    context: Optional[ContextTypes.DEFAULT_TYPE] = None,
    chat_id: Optional[int] = None,
) -> Tuple[Optional[httpx.Response], Optional[dict]]:
    """Call Mataroa API and return (response, parsed_json) with a single retry on error."""
    url = API_URL if slug is None else f"{API_URL}{slug}/"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async def _request_once() -> Tuple[Optional[httpx.Response], Optional[dict]]:
        async with httpx.AsyncClient() as client:
            response = await client.request(
                method, url, headers=headers, json=payload, timeout=HTTP_TIMEOUT
            )
        data = None
        if response is not None:
            if response.status_code == 204:
                return response, None
            ct = (response.headers.get("Content-Type", "") or "").lower()
            if response.content and "application/json" in ct:
                try:
                    data = response.json()
                except JSONDecodeError:
                    data = None
        return response, data

    try:
        if context and chat_id:
            await send_typing(context, chat_id)
        response, data = await _request_once()
        # Retry once on server errors
        if retry and response is not None and response.status_code >= 500:
            response, data = await _request_once()
        return response, data
    except httpx.HTTPError as e:
        logger.error("API call error: %s", e)
        if retry:
            try:
                response, data = await _request_once()
                return response, data
            except httpx.HTTPError as e2:
                logger.error("API retry error: %s", e2)
        return None, None


# ---------- Common UI Builders ----------

def open_url_button(url: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(MESSAGES["BUTTON_OPEN"], url=url)


def share_url_button(url: str) -> InlineKeyboardButton:
    # Telegram doesn't support copy-to-clipboard; using URL open for share
    return InlineKeyboardButton(MESSAGES["BUTTON_SHARE"], url=url)


# ---------- Token Mapping Helpers ----------

def _get_user_bucket(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> Dict[str, Any]:
    try:
        app_ud = context.application.user_data  # type: ignore[attr-defined]
    except Exception:
        app_ud = {}
    ud = app_ud.get(user_id)
    if ud is None:
        ud = {}
        app_ud[user_id] = ud
    return ud


def get_token_maps(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> Tuple[Dict[str, str], Dict[str, str]]:
    ud = _get_user_bucket(context, user_id)
    t2s = ud.get("tokens")
    if t2s is None:
        t2s = {}
        ud["tokens"] = t2s
    s2t = ud.get("rev_tokens")
    if s2t is None:
        s2t = {}
        ud["rev_tokens"] = s2t
    return t2s, s2t


def _gen_token(existing: Set[str]) -> str:
    # 8-char base36 token
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    while True:
        n = secrets.randbits(40)
        s = ""
        while n:
            n, r = divmod(n, 36)
            s = alphabet[r] + s
        if not s:
            s = "0"
        token = s[:8] if len(s) >= 8 else s.rjust(8, "0")
        if token not in existing:
            return token


def token_for_slug(context: ContextTypes.DEFAULT_TYPE, user_id: int, slug: str) -> str:
    t2s, s2t = get_token_maps(context, user_id)
    if slug in s2t:
        return s2t[slug]
    token = _gen_token(set(t2s.keys()))
    t2s[token] = slug
    s2t[slug] = token
    return token


def resolve_token_to_slug(context: ContextTypes.DEFAULT_TYPE, user_id: int, token_or_slug: str) -> Optional[str]:
    t2s, s2t = get_token_maps(context, user_id)
    if token_or_slug in t2s:
        return t2s[token_or_slug]
    # Back-compat: if value itself looks like a slug, accept and register
    if is_valid_slug(token_or_slug):
        token_for_slug(context, user_id, token_or_slug)
        return token_or_slug
    return None


# ---------- List Keyboard Builders ----------

def list_nav_keyboard(
    filter_mode: str, page: int, total_pages: int
) -> List[List[InlineKeyboardButton]]:
    """Build navigation and filter controls for the posts list view."""
    btns = []
    # Filters
    btns.append(
        [
            InlineKeyboardButton(
                f"{MESSAGES['FILTER_ALL']}{'✓' if filter_mode=='all' else ''}", callback_data=f"{CB_LIST_FILTER_PREFIX}all"
            ),
            InlineKeyboardButton(
                f"{MESSAGES['FILTER_PUBLISHED']}{'✓' if filter_mode=='published' else ''}",
                callback_data=f"{CB_LIST_FILTER_PREFIX}published",
            ),
            InlineKeyboardButton(
                f"{MESSAGES['FILTER_DRAFTS']}{'✓' if filter_mode=='drafts' else ''}", callback_data=f"{CB_LIST_FILTER_PREFIX}drafts"
            ),
        ]
    )
    # Paging
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(MESSAGES["BUTTON_PREV"], callback_data=f"{CB_LIST_PAGE_PREFIX}prev"))
    nav.append(InlineKeyboardButton(MESSAGES["BUTTON_PAGE_X_OF_Y"].format(page=page, total=total_pages), callback_data=CB_LIST_NOP))
    if page < total_pages:
        nav.append(InlineKeyboardButton(MESSAGES["BUTTON_NEXT"], callback_data=f"{CB_LIST_PAGE_PREFIX}next"))
    btns.append(nav)
    # Refresh
    btns.append([InlineKeyboardButton(MESSAGES["BUTTON_REFRESH"], callback_data=CB_LIST_REFRESH)])
    return btns


def build_post_row_buttons(post: dict, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> List[List[InlineKeyboardButton]]:
    """Build per-post single 'Manage • <title>' button for list view using tokenized callback data."""
    slug = post.get("slug", "")
    title = post.get("title", "No Title")
    label = f"Manage • {title}"
    token = token_for_slug(context, user_id, slug) if slug else ""
    return [[InlineKeyboardButton(label, callback_data=f"{CB_LIST_PREFIX}manage:{token}")]]


def build_list_message(
    posts: List[dict],
    filter_mode: str,
    page: int,
    query: Optional[str],
    preview_length: int,
    context: Optional[ContextTypes.DEFAULT_TYPE] = None,
    user_id: Optional[int] = None,
) -> Tuple[str, InlineKeyboardMarkup, int]:
    """Build list text (MarkdownV2) and keyboard; returns (message, keyboard, page_used)."""
    # Filter
    filtered = posts
    if filter_mode == "published":
        filtered = [p for p in posts if p.get("published_at")]
    elif filter_mode == "drafts":
        filtered = [p for p in posts if not p.get("published_at")]
    if query:
        q = query.lower()
        filtered = [
            p
            for p in filtered
            if q in (p.get("title", "").lower() + " " + p.get("body", "").lower())
        ]

    total = len(filtered)
    total_pages = max(1, (total + POSTS_PAGE_SIZE - 1) // POSTS_PAGE_SIZE)
    page_clamped = max(1, min(page, total_pages))
    start = (page_clamped - 1) * POSTS_PAGE_SIZE
    page_posts = filtered[start : start + POSTS_PAGE_SIZE]

    header = mdv2(MESSAGES["LIST_HEADER"].format(total=total))
    lines: List[str] = [header]
    for p in page_posts:
        title = p.get("title", "No Title")
        slug = p.get("slug", "")
        is_pub = bool(p.get("published_at"))
        status = "🟢" if is_pub else "📝"
        preview_raw = truncate(p.get("body", ""), preview_length)
        title_md = mdv2(title)
        slug_md = mdv2(slug)
        preview_md = mdv2(preview_raw)
        lines.append(f"\n{status} *{title_md}*\n{slug_md}\n{preview_md}")
    if not page_posts:
        lines.append(f"\n{mdv2(MESSAGES['LIST_NO_MATCH'])}")

    # Build keyboard with per post manage buttons and nav
    rows: List[List[InlineKeyboardButton]] = []
    for p in page_posts:
        if context is not None and user_id is not None:
            rows += build_post_row_buttons(p, context, user_id)
        else:
            # Fallback (should not happen): disable manage if no context/user
            pass
    rows += list_nav_keyboard(filter_mode, page_clamped, total_pages)

    return "\n".join(lines), InlineKeyboardMarkup(rows), page_clamped


# ---------- Private Chat Helpers ----------

def is_private_chat(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat and chat.type == "private")


async def ensure_private(
    update: Update, context: ContextTypes.DEFAULT_TYPE, message: str = MESSAGES["PRIVACY_DM"]
) -> bool:
    if is_private_chat(update):
        return True
    if update.callback_query:
        try:
            await update.callback_query.answer(message, show_alert=True)
        except Exception:
            pass
    if getattr(update, "message", None):
        try:
            await update.message.reply_text(message)
        except Exception:
            pass
    return False


# ---------- HTTP/Response Helpers ----------

def http_2xx(response: Optional[httpx.Response]) -> bool:
    return bool(response is not None and 200 <= response.status_code < 300)


def payload_ok(data: Optional[dict]) -> bool:
    return isinstance(data, dict) and bool(data.get("ok"))


# ---------- Messaging Helper ----------
async def send_or_edit(
    text: str,
    *,
    update: Optional[Update] = None,
    query: Optional[Any] = None,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    markdown: bool = False,
    escape_user: bool = False,
    disable_web_preview: bool = True,
) -> None:
    """Send a message or edit an existing callback message with consistent options."""
    try:
        pm = "MarkdownV2" if markdown else None
        to_send = mdv2(text) if (markdown and escape_user) else text
        if query is not None:
            await query.edit_message_text(
                to_send,
                reply_markup=reply_markup,
                parse_mode=pm,
                disable_web_page_preview=disable_web_preview,
            )
            return
        if update is not None and getattr(update, "message", None) is not None:
            await update.message.reply_text(
                to_send,
                reply_markup=reply_markup,
                parse_mode=pm,
                disable_web_page_preview=disable_web_preview,
            )
    except Exception:
        pass


def invalid_slug_text() -> str:
    return MESSAGES["INVALID_SLUG"]


def get_list_state(context: ContextTypes.DEFAULT_TYPE) -> Dict[str, Any]:
    state = context.user_data.get(K_LIST_STATE)
    if not isinstance(state, dict):
        state = {"filter": "all", "page": 1, "query": None}
        context.user_data[K_LIST_STATE] = state
    return state


def set_list_state(context: ContextTypes.DEFAULT_TYPE, state: Dict[str, Any]) -> None:
    context.user_data[K_LIST_STATE] = state


def get_draft_parts(context: ContextTypes.DEFAULT_TYPE) -> List[str]:
    parts = context.user_data.get(K_BODY_PARTS)
    if not isinstance(parts, list):
        parts = []
        context.user_data[K_BODY_PARTS] = parts
    return parts


def set_draft_parts(context: ContextTypes.DEFAULT_TYPE, parts: List[str]) -> None:
    context.user_data[K_BODY_PARTS] = list(parts)


async def gate_callback(query: Any, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Apply cooldown gating for callback queries and answer consistently."""
    if not cooldown_ok(context):
        try:
            await query.answer(MESSAGES["PLEASE_WAIT"], show_alert=False)
        except Exception:
            pass
        return False
    try:
        await query.answer()
    except Exception:
        pass
    return True


# ---------- Command Handlers ----------

# /start: Set API key
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat and chat.type != "private":
        await update.message.reply_text(MESSAGES["SET_API_PRIVATE"])
        return ConversationHandler.END
    if not await ensure_allowed(update, context):
        return ConversationHandler.END
    await update.message.reply_text(
        MESSAGES["START_WELCOME"],
        reply_markup=cancel_keyboard(),
    )
    return ENTER_API_KEY


async def enter_api_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat and chat.type != "private":
        await update.message.reply_text(MESSAGES["SET_API_PRIVATE"])
        return ConversationHandler.END
    if not await ensure_allowed(update, context):
        return ConversationHandler.END
    user_id = update.message.from_user.id
    api_key = update.message.text.strip()
    users_data[user_id] = users_data.get(user_id, UserData(api_key=api_key))
    users_data[user_id].api_key = api_key
    await save_users_data()
    await update.message.reply_text(MESSAGES["API_SAVED"])
    return ConversationHandler.END


# /help: Show help information
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_allowed(update, context):
        return
    await send_or_edit(MESSAGES["HELP_TEXT"], update=update)


# Global cancel handler (for messages & inline keyboards)
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_allowed(update, context):
        return ConversationHandler.END
    if update.callback_query:
        try:
            await update.callback_query.answer()
        except Exception:
            pass
        await send_or_edit(MESSAGES["OP_CANCELLED"], update=update, query=update.callback_query)
    else:
        await update.message.reply_text(MESSAGES["OP_CANCELLED"])
    return ConversationHandler.END


# ----- New Post Flow -----
async def post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_allowed(update, context):
        return ConversationHandler.END
    if not await ensure_private(update, context):
        return ConversationHandler.END
    user_id = await ensure_api_key_or_prompt(update, context)
    if not user_id:
        return ConversationHandler.END

    # Resume last draft if exists
    u = users_data[user_id]
    set_draft_parts(context, list(u.draft_parts) if u.draft_parts else [])
    context.user_data[K_UNDO_STACK] = list(u.undo_stack) if u.undo_stack else []
    if u.draft_title:
        context.user_data[K_TITLE] = u.draft_title
        await update.message.reply_text(
            MESSAGES["RESUME_DRAFT"].format(title=u.draft_title),
            reply_markup=drafting_keyboard(),
        )
        return ENTER_BODY

    await update.message.reply_text(MESSAGES["ENTER_TITLE_PROMPT"], reply_markup=cancel_keyboard())
    return ENTER_TITLE


async def new_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Fast path: /new Title | Body
    if not await ensure_allowed(update, context):
        return ConversationHandler.END
    if not await ensure_private(update, context):
        return ConversationHandler.END
    user_id = await ensure_api_key_or_prompt(update, context)
    if not user_id:
        return ConversationHandler.END

    text = update.message.text or ""
    parts = text.split(" ", 1)
    arg = parts[1] if len(parts) > 1 else ""
    title = ""
    body = ""
    if "|" in arg:
        t, b = arg.split("|", 1)
        title = t.strip()
        body = b.strip()
    else:
        title = arg.strip()
    if title:
        context.user_data[K_TITLE] = title
        users_data[user_id].draft_title = title
        await save_users_data()
        if body:
            # Direct to publish choice
            context.user_data[K_BODY] = body
            default_mode = users_data[user_id].settings.get("default_publish_mode", "draft")
            await update.message.reply_text(
                MESSAGES["CHOOSE_PUBLICATION_OPTION"],
                reply_markup=publish_choice_keyboard(default_mode=default_mode),
            )
            return ENTER_PUBLISH_CHOICE
        else:
            await update.message.reply_text(
                MESSAGES["NOW_SEND_BODY"],
                reply_markup=drafting_keyboard(),
            )
            return ENTER_BODY
    else:
        # Fallback to normal /post flow
        return await post(update, context)


async def enter_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_allowed(update, context):
        return ENTER_TITLE
    title = update.message.text.strip()
    if not title:
        await update.message.reply_text(MESSAGES["PROMPT_VALID_TITLE"], reply_markup=cancel_keyboard())
        return ENTER_TITLE
    context.user_data[K_TITLE] = title
    # Persist as draft title for resume
    u = users_data[update.message.from_user.id]
    u.draft_title = title
    await save_users_data()
    await update.message.reply_text(
        MESSAGES["SEND_BODY_MULTIMSG"],
        reply_markup=drafting_keyboard(),
    )
    return ENTER_BODY


async def enter_body(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_allowed(update, context):
        return ENTER_BODY
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text(MESSAGES["PROMPT_VALID_CONTENT"], reply_markup=drafting_keyboard())
        return ENTER_BODY
    parts = get_draft_parts(context)
    parts.append(text)
    set_draft_parts(context, parts)
    # Maintain undo stack
    undo_stack = context.user_data.get(K_UNDO_STACK, [])
    undo_stack.append(text)
    context.user_data[K_UNDO_STACK] = undo_stack

    # Persist draft to users_data for resume
    u = users_data[update.message.from_user.id]
    u.draft_parts = list(parts)
    u.undo_stack = list(undo_stack)
    await save_users_data()

    await update.message.reply_text(MESSAGES["ADDED_TO_DRAFT"], reply_markup=drafting_keyboard())
    return ENTER_BODY


async def draft_preview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_allowed(update, context):
        return ENTER_BODY
    parts = get_draft_parts(context)
    title = context.user_data.get(K_TITLE, "(no title)")
    body = "\n".join(parts)
    preview = render_create_preview(title, body, None)
    await update.message.reply_text(
        preview, parse_mode="MarkdownV2", reply_markup=drafting_keyboard()
    )
    return ENTER_BODY


async def draft_preview_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await ensure_allowed(update, context):
        return ENTER_BODY
    if not await ensure_private(update, context):
        return ENTER_BODY
    if not await gate_callback(query, context):
        return ENTER_BODY
    parts = get_draft_parts(context)
    title = context.user_data.get(K_TITLE, "(no title)")
    body = "\n".join(parts)
    preview = render_create_preview(title, body, None)
    await query.edit_message_text(preview, parse_mode="MarkdownV2", reply_markup=drafting_keyboard())
    return ENTER_BODY


async def draft_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_allowed(update, context):
        return ENTER_BODY
    set_draft_parts(context, [])
    context.user_data[K_UNDO_STACK] = []
    u = users_data[update.message.from_user.id]
    u.draft_parts = []
    u.undo_stack = []
    await save_users_data()
    await update.message.reply_text(MESSAGES["DRAFT_CLEARED"], reply_markup=drafting_keyboard())
    return ENTER_BODY


async def draft_clear_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await ensure_allowed(update, context):
        return ENTER_BODY
    if not await ensure_private(update, context):
        return ENTER_BODY
    if not await gate_callback(query, context):
        return ENTER_BODY
    set_draft_parts(context, [])
    context.user_data[K_UNDO_STACK] = []
    u = users_data[query.from_user.id]
    u.draft_parts = []
    u.undo_stack = []
    await save_users_data()
    await query.edit_message_text(MESSAGES["DRAFT_CLEARED"], reply_markup=drafting_keyboard())
    return ENTER_BODY


async def draft_undo_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await ensure_allowed(update, context):
        return ENTER_BODY
    if not await ensure_private(update, context):
        return ENTER_BODY
    if not await gate_callback(query, context):
        return ENTER_BODY
    undo_stack = context.user_data.get(K_UNDO_STACK, [])
    if undo_stack:
        last = undo_stack.pop()
        parts = get_draft_parts(context)
        if parts and parts[-1] == last:
            parts.pop()
        context.user_data[K_UNDO_STACK] = undo_stack
        set_draft_parts(context, parts)
        u = users_data[query.from_user.id]
        u.draft_parts = list(parts)
        u.undo_stack = list(undo_stack)
        await save_users_data()
        await query.edit_message_text(MESSAGES["REMOVED_LAST_CHUNK"], reply_markup=drafting_keyboard())
    else:
        await query.edit_message_text(MESSAGES["NOTHING_TO_UNDO"], reply_markup=drafting_keyboard())
    return ENTER_BODY


async def draft_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_allowed(update, context):
        return ConversationHandler.END
    parts = get_draft_parts(context)
    if not parts:
        await update.message.reply_text(MESSAGES["DRAFT_EMPTY"], reply_markup=drafting_keyboard())
        return ENTER_BODY
    context.user_data[K_BODY] = "\n".join(parts)
    user_id = update.message.from_user.id
    default_mode = users_data[user_id].settings.get("default_publish_mode", "draft")
    await update.message.reply_text(
        MESSAGES["CHOOSE_PUBLICATION_OPTION"],
        reply_markup=publish_choice_keyboard(default_mode=default_mode),
    )
    return ENTER_PUBLISH_CHOICE


async def draft_done_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await ensure_allowed(update, context):
        return ConversationHandler.END
    if not await ensure_private(update, context):
        return ConversationHandler.END
    if not await gate_callback(query, context):
        return ENTER_BODY
    parts = get_draft_parts(context)
    if not parts:
        await query.edit_message_text(MESSAGES["DRAFT_EMPTY"], reply_markup=drafting_keyboard())
        return ENTER_BODY
    context.user_data[K_BODY] = "\n".join(parts)
    user_id = query.from_user.id
    default_mode = users_data[user_id].settings.get("default_publish_mode", "draft")
    await query.edit_message_text(
        MESSAGES["CHOOSE_PUBLICATION_OPTION"],
        reply_markup=publish_choice_keyboard(default_mode=default_mode),
    )
    return ENTER_PUBLISH_CHOICE


async def template_insert_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await ensure_allowed(update, context):
        return ENTER_BODY
    if not await ensure_private(update, context):
        return ENTER_BODY
    if not await gate_callback(query, context):
        return ENTER_BODY
    _, key = query.data.split(":", 1)
    tpl = TEMPLATES.get(key)
    if tpl:
        parts = get_draft_parts(context)
        parts.append(tpl)
        set_draft_parts(context, parts)
        undo_stack = context.user_data.get(K_UNDO_STACK, [])
        undo_stack.append(tpl)
        context.user_data[K_UNDO_STACK] = undo_stack
        # Persist
        u = users_data[query.from_user.id]
        u.draft_parts = list(parts)
        u.undo_stack = list(undo_stack)
        await save_users_data()
        await query.edit_message_text(
            f"📎 Inserted template '{key}'.", reply_markup=drafting_keyboard()
        )
    else:
        await query.edit_message_text("Template not found.", reply_markup=drafting_keyboard())
    return ENTER_BODY


async def post_publish_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await ensure_allowed(update, context):
        return ConversationHandler.END
    if not await ensure_private(update, context):
        return ConversationHandler.END
    if not await gate_callback(query, context):
        return ENTER_PUBLISH_CHOICE
    if query.data == CB_CANCEL:
        return await cancel(update, context)
    if query.data not in (CB_CHOICE_DRAFT, CB_CHOICE_PUBLISH):
        await query.answer(MESSAGES["INVALID_ACTION"])  # best effort
        return ENTER_PUBLISH_CHOICE
    choice = query.data
    published_at = None if choice == CB_CHOICE_DRAFT else now_date_str()
    context.user_data[K_PUBLISHED_AT] = published_at
    # Show preview with confirmation options
    preview = render_create_preview(
        context.user_data[K_TITLE], context.user_data[K_BODY], published_at
    )
    reply_markup = preview_submit_keyboard(include_slug_sync=False)
    await query.edit_message_text(preview, parse_mode="MarkdownV2", reply_markup=reply_markup)
    return CONFIRM_POST


async def _submit_create_post(query: Any, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Submit a create-post request based on context state and render outcome in place."""
    user_id = query.from_user.id
    api_key = users_data[user_id].api_key
    payload = {
        "title": context.user_data[K_TITLE],
        "body": context.user_data[K_BODY],
        "published_at": context.user_data.get(K_PUBLISHED_AT),
    }
    # Save for retry
    users_data[user_id].last_action = {"type": "create", "payload": payload}
    await save_users_data()

    chat_id = safe_chat_id(query=query)
    response, data = await api_call(
        "POST", api_key, payload=payload, context=context, chat_id=chat_id
    )
    success = http_2xx(response) or payload_ok(data)
    if success:
        slug = data.get("slug") if isinstance(data, dict) else None
        url = data.get("url") if isinstance(data, dict) else None
        # Clear draft after success
        u = users_data[user_id]
        u.draft_title = ""
        u.draft_parts = []
        u.undo_stack = []
        u.last_action = {}
        await save_users_data()
        # Add inline buttons for immediate Edit and Delete actions.
        rows: List[List[InlineKeyboardButton]] = []
        if slug:
            tok = token_for_slug(context, user_id, slug)
            rows.append(
                [
                    InlineKeyboardButton(MESSAGES["BUTTON_EDIT"], callback_data=f"{CB_EDIT_PREFIX}{tok}"),
                    InlineKeyboardButton(MESSAGES["BUTTON_DELETE"], callback_data=f"{CB_DELETE_PREFIX}{tok}"),
                ]
            )
        if is_valid_absolute_url(url or ""):
            rows.append([open_url_button(url)])
        keyboard = InlineKeyboardMarkup(rows) if rows else None
        published_status = "Published" if context.user_data.get(K_PUBLISHED_AT) else "Draft"
        if slug or url:
            await query.edit_message_text(
                MESSAGES["POST_CREATED_WITH_DETAILS"].format(slug=slug, status=published_status, url=url),
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
        else:
            await query.edit_message_text(MESSAGES["POST_CREATED"], disable_web_page_preview=True)
    else:
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(MESSAGES["BUTTON_RETRY"], callback_data=CB_RETRY_CREATE)]]
        )
        await query.edit_message_text(MESSAGES["FAILED_CREATE_POST"], reply_markup=keyboard)


async def confirm_post_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await ensure_allowed(update, context):
        return ConversationHandler.END
    if not await ensure_private(update, context):
        return ConversationHandler.END
    if not await gate_callback(query, context):
        return CONFIRM_POST
    if query.data == CB_CANCEL:
        return await cancel(update, context)
    if query.data != CB_SUBMIT_POST:
        await query.answer(MESSAGES["INVALID_OR_STALE_ACTION"])  # best effort
        return CONFIRM_POST
    await _submit_create_post(query, context)
    return ConversationHandler.END


# ----- Update Post Flow -----
async def update_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_allowed(update, context):
        return ConversationHandler.END
    if not await ensure_private(update, context):
        return ConversationHandler.END
    user_id = await ensure_api_key_or_prompt(update, context)
    if not user_id:
        return ConversationHandler.END
    await update.message.reply_text(
        MESSAGES["ENTER_UPDATE_SLUG_PROMPT"], reply_markup=cancel_keyboard()
    )
    return ENTER_UPDATE_SLUG


# New entry point for inline edit button.
async def inline_edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await ensure_allowed(update, context):
        return ConversationHandler.END
    if not await ensure_private(update, context):
        return ConversationHandler.END
    if not await gate_callback(query, context):
        return ConversationHandler.END
    # Ensure API key configured
    user_id = await ensure_api_key_or_prompt(update, context)
    if not user_id:
        return ConversationHandler.END
    tok = query.data.split(CB_EDIT_PREFIX, 1)[1]
    slug = resolve_token_to_slug(context, user_id, tok)
    if not slug or not is_valid_slug(slug):
        await query.edit_message_text(invalid_slug_text())
        return ConversationHandler.END
    context.user_data[K_SLUG] = slug
    api_key = users_data[user_id].api_key
    chat_id = safe_chat_id(query=query)
    response, res_data = await api_call(
        "GET", api_key, slug=slug, context=context, chat_id=chat_id
    )
    ok_shape = isinstance(res_data, dict) and (
        "title" in res_data or "body" in res_data or "slug" in res_data
    )
    if ((http_2xx(response) and ok_shape) or payload_ok(res_data)):
        current_title = res_data.get("title", "N/A") if isinstance(res_data, dict) else "N/A"
        current_body = res_data.get("body", "N/A") if isinstance(res_data, dict) else "N/A"
        context.user_data[K_CURRENT_SLUG] = slug
        context.user_data[K_CURRENT_TITLE] = current_title
        context.user_data[K_CURRENT_BODY] = current_body
        context.user_data[K_SLUG_SYNC] = False
        message = (
            f"*{mdv2(MESSAGES['CURRENT_TITLE_LABEL'])}:*\n{mdv2(current_title)}\n\n"
            f"*{mdv2(MESSAGES['CURRENT_BODY_LABEL'])}:*\n{mdv2(truncate(current_body, 500))}\n\n"
            f"{mdv2(MESSAGES['ENTER_UPDATED_TITLE'])}"
        )
        await query.edit_message_text(message, parse_mode="MarkdownV2", reply_markup=cancel_keyboard())
        return ENTER_UPDATED_TITLE
    else:
        await query.edit_message_text(MESSAGES["FAILED_FETCH_POST_DETAILS"])
        return ConversationHandler.END


async def enter_update_slug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_allowed(update, context):
        return ENTER_UPDATE_SLUG
    slug = update.message.text.strip()
    if not slug or not is_valid_slug(slug):
        await update.message.reply_text(MESSAGES["PROMPT_VALID_SLUG"], reply_markup=cancel_keyboard())
        return ENTER_UPDATE_SLUG
    context.user_data[K_SLUG] = slug
    user_id = update.message.from_user.id
    api_key = users_data[user_id].api_key
    response, data = await api_call(
        "GET", api_key, slug=slug, context=context, chat_id=safe_chat_id(update)
    )
    ok_shape = isinstance(data, dict) and ("title" in data or "body" in data or "slug" in data)
    if ((http_2xx(response) and ok_shape) or payload_ok(data)):
        current_title = data.get("title", "N/A") if isinstance(data, dict) else "N/A"
        current_body = data.get("body", "N/A") if isinstance(data, dict) else "N/A"
        context.user_data[K_CURRENT_SLUG] = slug
        context.user_data[K_CURRENT_TITLE] = current_title
        context.user_data[K_CURRENT_BODY] = current_body
        context.user_data[K_SLUG_SYNC] = False
        message = (
            f"*{mdv2(MESSAGES['CURRENT_TITLE_LABEL'])}:*\n{mdv2(current_title)}\n\n"
            f"*{mdv2(MESSAGES['CURRENT_BODY_LABEL'])}:*\n{mdv2(truncate(current_body, 500))}\n\n"
            f"{mdv2(MESSAGES['ENTER_UPDATED_TITLE'])}"
        )
        await update.message.reply_text(
            message, parse_mode="MarkdownV2", reply_markup=cancel_keyboard()
        )
        return ENTER_UPDATED_TITLE
    else:
        await update.message.reply_text(
            MESSAGES["FAILED_FETCH_POST_DETAILS_CHECK"]
        )
        return ConversationHandler.END


async def enter_updated_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_allowed(update, context):
        return ENTER_UPDATED_TITLE
    updated_title = update.message.text.strip()
    if not updated_title:
        await update.message.reply_text(MESSAGES["PROMPT_VALID_TITLE"], reply_markup=cancel_keyboard())
        return ENTER_UPDATED_TITLE
    context.user_data[K_TITLE] = updated_title
    # Slug sync suggestion
    current_slug = context.user_data.get(K_CURRENT_SLUG, context.user_data.get(K_SLUG))
    suggested = slugify(updated_title)
    context.user_data[K_SLUG_SUGGESTED] = suggested
    context.user_data[K_SLUG_SUGGESTED_VALID] = bool(suggested) and is_valid_slug(suggested)
    note = ""
    if current_slug and context.user_data[K_SLUG_SUGGESTED_VALID] and current_slug != suggested:
        note = MESSAGES["SLUG_SUGGESTION_NOTE_MD"].format(current=mdv2(current_slug), suggested=mdv2(suggested))
    msg = mdv2(MESSAGES["ENTER_UPDATED_BODY"]) + note
    await update.message.reply_text(msg, reply_markup=cancel_keyboard(), parse_mode="MarkdownV2")
    return ENTER_UPDATED_BODY


async def enter_updated_body(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_allowed(update, context):
        return ENTER_UPDATED_BODY
    updated_body = update.message.text.strip()
    if not updated_body:
        await update.message.reply_text(MESSAGES["PROMPT_VALID_CONTENT"], reply_markup=cancel_keyboard())
        return ENTER_UPDATED_BODY
    context.user_data[K_BODY] = updated_body
    user_id = update.message.from_user.id
    default_mode = users_data[user_id].settings.get("default_publish_mode", "draft")
    await update.message.reply_text(
        MESSAGES["CHOOSE_PUBLICATION_OPTION"],
        reply_markup=publish_choice_keyboard(default_mode=default_mode),
    )
    return ENTER_PUBLISH_CHOICE_UPDATE


async def update_publish_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await ensure_allowed(update, context):
        return ConversationHandler.END
    if not await ensure_private(update, context):
        return ConversationHandler.END
    if not await gate_callback(query, context):
        return ENTER_PUBLISH_CHOICE_UPDATE
    if query.data == CB_CANCEL:
        return await cancel(update, context)
    if query.data not in (CB_CHOICE_DRAFT, CB_CHOICE_PUBLISH):
        await query.answer(MESSAGES["INVALID_ACTION"])  # best effort
        return ENTER_PUBLISH_CHOICE_UPDATE
    choice = query.data
    published_at = None if choice == CB_CHOICE_DRAFT else now_date_str()
    context.user_data[K_PUBLISHED_AT] = published_at
    current_slug = context.user_data.get(K_CURRENT_SLUG, context.user_data.get(K_SLUG))
    suggested = context.user_data.get(K_SLUG_SUGGESTED, current_slug)
    valid_suggested = bool(suggested) and is_valid_slug(suggested) and suggested != current_slug
    slug_sync = context.user_data.get(K_SLUG_SYNC, False) if valid_suggested else False
    context.user_data[K_SLUG_SYNC] = slug_sync
    final_slug = suggested if slug_sync else current_slug
    preview = render_update_preview(
        context.user_data[K_TITLE],
        context.user_data[K_BODY],
        published_at,
        current_slug,
        suggested if valid_suggested else current_slug,
        slug_sync,
    )
    reply_markup = update_preview_submit_keyboard(include_slug_sync=valid_suggested)
    await query.edit_message_text(preview, parse_mode="MarkdownV2", reply_markup=reply_markup)
    context.user_data[K_FINAL_SLUG] = final_slug
    return CONFIRM_UPDATE


async def toggle_slug_sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await ensure_allowed(update, context):
        return CONFIRM_UPDATE
    if not await ensure_private(update, context):
        return CONFIRM_UPDATE
    if not await gate_callback(query, context):
        return CONFIRM_UPDATE
    current_slug = context.user_data.get(K_CURRENT_SLUG, context.user_data.get(K_SLUG))
    suggested = context.user_data.get(K_SLUG_SUGGESTED, current_slug)
    if not suggested or not is_valid_slug(suggested) or suggested == current_slug:
        await query.answer(MESSAGES["SLUG_SUGGESTION_UNAVAILABLE"], show_alert=True)
        return CONFIRM_UPDATE
    context.user_data[K_SLUG_SYNC] = not context.user_data.get(K_SLUG_SYNC, False)
    # Re-render preview
    slug_sync = context.user_data.get(K_SLUG_SYNC, False)
    published_at = context.user_data.get(K_PUBLISHED_AT)
    preview = render_update_preview(
        context.user_data[K_TITLE],
        context.user_data[K_BODY],
        published_at,
        current_slug,
        suggested,
        slug_sync,
    )
    reply_markup = update_preview_submit_keyboard(include_slug_sync=True)
    await query.edit_message_text(preview, parse_mode="MarkdownV2", reply_markup=reply_markup)
    context.user_data[K_FINAL_SLUG] = suggested if slug_sync else current_slug
    return CONFIRM_UPDATE


async def _submit_update_post(query: Any, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Submit an update-post request based on context state and render outcome in place."""
    user_id = query.from_user.id
    api_key = users_data[user_id].api_key
    slug = context.user_data.get(K_CURRENT_SLUG, context.user_data.get(K_SLUG))
    payload = {
        "title": context.user_data[K_TITLE],
        "body": context.user_data[K_BODY],
        "published_at": context.user_data.get(K_PUBLISHED_AT),
    }
    final_slug = context.user_data.get(K_FINAL_SLUG, slug)
    if not is_valid_slug(final_slug or ""):
        final_slug = slug
    if final_slug and final_slug != slug:
        payload["slug"] = final_slug

    # Save for retry
    users_data[user_id].last_action = {"type": "update", "slug": slug, "payload": payload}
    await save_users_data()

    chat_id = safe_chat_id(query=query)
    response, data = await api_call(
        "PATCH", api_key, slug=slug, payload=payload, context=context, chat_id=chat_id
    )
    success = http_2xx(response) or payload_ok(data)
    if success:
        url = data.get("url") if isinstance(data, dict) else None
        new_slug = data.get("slug", final_slug) if isinstance(data, dict) else final_slug
        rows: List[List[InlineKeyboardButton]] = []
        if new_slug:
            tok = token_for_slug(context, user_id, new_slug)
            rows.append(
                [
                    InlineKeyboardButton(MESSAGES["BUTTON_EDIT"], callback_data=f"{CB_EDIT_PREFIX}{tok}"),
                    InlineKeyboardButton(MESSAGES["BUTTON_DELETE"], callback_data=f"{CB_DELETE_PREFIX}{tok}"),
                ]
            )
        if is_valid_absolute_url(url or ""):
            rows.append([open_url_button(url)])
        keyboard = InlineKeyboardMarkup(rows) if rows else None
        try:
            await query.edit_message_text(
                MESSAGES["POST_UPDATED_WITH_DETAILS"].format(slug=new_slug, url=url), reply_markup=keyboard, disable_web_page_preview=True
            )
        except Exception:
            pass
        # Clear last action on success
        try:
            users_data[user_id].last_action = {}
            await save_users_data()
        except Exception:
            pass
    else:
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(MESSAGES["BUTTON_RETRY"], callback_data=CB_RETRY_UPDATE)]]
        )
        await query.edit_message_text(MESSAGES["FAILED_UPDATE_POST"], reply_markup=keyboard)


async def confirm_update_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await ensure_allowed(update, context):
        return ConversationHandler.END
    if not await ensure_private(update, context):
        return ConversationHandler.END
    if not await gate_callback(query, context):
        return CONFIRM_UPDATE
    if query.data == CB_CANCEL:
        return await cancel(update, context)
    if query.data != CB_SUBMIT_UPDATE:
        await query.answer(MESSAGES["INVALID_OR_STALE_ACTION"])  # best effort
        return CONFIRM_UPDATE
    await _submit_update_post(query, context)
    return ConversationHandler.END


# ----- Delete Post Flow -----

def get_pending_deletes_map(
    context: ContextTypes.DEFAULT_TYPE, user_id: int
) -> Dict[str, Any]:
    """Ensure and return the application's per-user pending deletes map."""
    try:
        app_ud = context.application.user_data  # type: ignore[attr-defined]
    except Exception:
        app_ud = {}
    ud = app_ud.get(user_id)
    if ud is None:
        ud = {}
        app_ud[user_id] = ud
    pending = ud.get("pending_deletes")
    if pending is None:
        pending = {}
        ud["pending_deletes"] = pending
    return pending


async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_allowed(update, context):
        return ConversationHandler.END
    if not await ensure_private(update, context):
        return ConversationHandler.END
    user_id = await ensure_api_key_or_prompt(update, context)
    if not user_id:
        return ConversationHandler.END
    if context.args:
        slug_arg = context.args[0]
        if not is_valid_slug(slug_arg):
            await update.message.reply_text(MESSAGES["PROMPT_VALID_SLUG"])
            return ENTER_DELETE_SLUG
        context.user_data[K_SLUG] = slug_arg
        settings = users_data[user_id].settings
        if not settings.get("confirm_before_delete", True):
            await schedule_delete_with_undo_message(update.message, context, slug_arg)
            return ConversationHandler.END
        return await confirm_delete_prompt(update, context)
    await update.message.reply_text(
        "✏️ Enter the slug of the post you want to delete:", reply_markup=cancel_keyboard()
    )
    return ENTER_DELETE_SLUG


# New entry point for inline delete button.
async def inline_delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await ensure_allowed(update, context):
        return ConversationHandler.END
    if not await ensure_private(update, context):
        return ConversationHandler.END
    if not await gate_callback(query, context):
        return ConversationHandler.END
    # Ensure API key configured
    user_id = await ensure_api_key_or_prompt(update, context)
    if not user_id:
        return ConversationHandler.END
    tok = query.data.split(CB_DELETE_PREFIX, 1)[1]
    slug = resolve_token_to_slug(context, user_id, tok)
    if not slug or not is_valid_slug(slug):
        await query.edit_message_text(invalid_slug_text())
        return ConversationHandler.END
    context.user_data[K_SLUG] = slug
    # If settings confirm-before-delete is False, schedule immediately with undo
    settings = users_data[user_id].settings
    if not settings.get("confirm_before_delete", True):
        await schedule_delete_with_undo(query, context, slug)
        return ConversationHandler.END
    return await confirm_delete_prompt(update, context)


async def enter_delete_slug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_allowed(update, context):
        return ENTER_DELETE_SLUG
    slug = update.message.text.strip()
    if not slug or not is_valid_slug(slug):
        await update.message.reply_text(MESSAGES["PROMPT_VALID_SLUG"], reply_markup=cancel_keyboard())
        return ENTER_DELETE_SLUG
    context.user_data[K_SLUG] = slug
    settings = users_data[update.message.from_user.id].settings
    if not settings.get("confirm_before_delete", True):
        await schedule_delete_with_undo_message(update.message, context, slug)
        return ConversationHandler.END
    return await confirm_delete_prompt(update, context)


async def confirm_delete_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton(MESSAGES["BUTTON_YES_DELETE"], callback_data=CB_CONFIRM_DELETE),
            InlineKeyboardButton(MESSAGES["BUTTON_CANCEL"], callback_data=CB_CANCEL),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await send_or_edit(
        MESSAGES["CONFIRM_DELETE"].format(slug=context.user_data[K_SLUG]),
        reply_markup=reply_markup,
        update=update,
        query=update.callback_query if update.callback_query else None,
    )
    # Always move to confirm state so the confirm handler is active
    return CONFIRM_DELETE


async def schedule_delete_with_undo(query: Any, context: ContextTypes.DEFAULT_TYPE, slug: str) -> None:
    """Schedule a delete job with an inline Undo button within DELETE_GRACE_SEC seconds."""
    user_id = query.from_user.id
    api_key = users_data[user_id].api_key
    chat_id = safe_chat_id(query=query)
    msg_id = query.message.message_id if getattr(query, "message", None) else None

    # Save for retry (delete)
    users_data[user_id].last_action = {"type": "delete", "slug": slug}
    await save_users_data()

    pending = get_pending_deletes_map(context, user_id)
    # Prevent double-scheduling: cancel existing
    existing = pending.get(slug)
    if existing:
        try:
            existing.schedule_removal()
        except Exception:
            pass
    # schedule job
    job = context.job_queue.run_once(
        execute_delete_job,
        DELETE_GRACE_SEC,
        data={
            "user_id": user_id,
            "api_key": api_key,
            "slug": slug,
            "chat_id": chat_id,
            "message_id": msg_id,
        },
    )
    pending[slug] = job

    tok = token_for_slug(context, user_id, slug)
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton(MESSAGES["BUTTON_UNDO_DELETE"], callback_data=f"{CB_UNDO_DELETE_PREFIX}{tok}")]]
    )
    await query.edit_message_text(
        MESSAGES["DELETING_IN"].format(slug=slug, seconds=DELETE_GRACE_SEC), reply_markup=kb
    )


async def schedule_delete_with_undo_message(message: Any, context: ContextTypes.DEFAULT_TYPE, slug: str) -> None:
    """Schedule a delete job (message context) with Undo within DELETE_GRACE_SEC seconds."""
    user_id = message.from_user.id
    api_key = users_data[user_id].api_key

    # Save for retry (delete)
    users_data[user_id].last_action = {"type": "delete", "slug": slug}
    await save_users_data()

    pending = get_pending_deletes_map(context, user_id)
    existing = pending.get(slug)
    if existing:
        try:
            existing.schedule_removal()
        except Exception:
            pass

    tok = token_for_slug(context, user_id, slug)
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton(MESSAGES["BUTTON_UNDO_DELETE"], callback_data=f"{CB_UNDO_DELETE_PREFIX}{tok}")]]
    )
    sent = await message.reply_text(
        MESSAGES["DELETING_IN"].format(slug=slug, seconds=DELETE_GRACE_SEC), reply_markup=kb
    )

    job = context.job_queue.run_once(
        execute_delete_job,
        DELETE_GRACE_SEC,
        data={
            "user_id": user_id,
            "api_key": api_key,
            "slug": slug,
            "chat_id": sent.chat_id,
            "message_id": sent.message_id,
        },
    )
    pending[slug] = job


async def execute_delete_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Execute the actual delete call after the grace period and edit the pending message."""
    # Job context
    data = context.job.data
    user_id = data["user_id"]
    api_key = data["api_key"]
    slug = data["slug"]
    chat_id = data["chat_id"]
    message_id = data.get("message_id")

    # Ensure per-user container exists and clean up pending entry if still present
    try:
        pending = get_pending_deletes_map(context, user_id)
        pending.pop(slug, None)
    except Exception:
        pass

    # Execute deletion
    try:
        response, _ = await api_call("DELETE", api_key, slug=slug)
        if http_2xx(response):
            # clear last action if it's this deletion
            try:
                if (
                    users_data.get(user_id)
                    and users_data[user_id].last_action.get("type") == "delete"
                    and users_data[user_id].last_action.get("slug") == slug
                ):
                    users_data[user_id].last_action = {}
                    await save_users_data()
            except Exception:
                pass
            msg = MESSAGES["POST_DELETED"].format(slug=slug)
            try:
                if chat_id is not None and message_id is not None:
                    await context.application.bot.edit_message_text(
                        chat_id=chat_id, message_id=message_id, text=msg, reply_markup=None
                    )
                elif chat_id is not None:
                    await context.application.bot.send_message(chat_id, text=msg)
            except Exception:
                try:
                    if chat_id is not None:
                        await context.application.bot.send_message(chat_id, text=msg)
                except Exception:
                    pass
        else:
            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton(MESSAGES["BUTTON_RETRY"], callback_data=CB_RETRY_DELETE)]]
            )
            try:
                if chat_id is not None and message_id is not None:
                    await context.application.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=MESSAGES["FAILED_DELETE_POST_FMT"].format(slug=slug),
                        reply_markup=kb,
                    )
                elif chat_id is not None:
                    await context.application.bot.send_message(
                        chat_id, text=MESSAGES["FAILED_DELETE_POST_FMT"].format(slug=slug), reply_markup=kb
                    )
            except Exception:
                try:
                    if chat_id is not None:
                        await context.application.bot.send_message(
                            chat_id, text=MESSAGES["FAILED_DELETE_POST_FMT"].format(slug=slug), reply_markup=kb
                        )
                except Exception:
                    pass
    except Exception as e:
        logger.error("Delete job failed: %s", e)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(MESSAGES["BUTTON_RETRY"], callback_data=CB_RETRY_DELETE)]])
        try:
            if chat_id is not None and message_id is not None:
                await context.application.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=MESSAGES["FAILED_DELETE_POST_FMT"].format(slug=slug),
                    reply_markup=kb,
                )
            elif chat_id is not None:
                await context.application.bot.send_message(
                    chat_id, text=MESSAGES["FAILED_DELETE_POST_FMT"].format(slug=slug), reply_markup=kb
                )
        except Exception:
            pass


async def undo_delete_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Undo action for a scheduled deletion if still pending."""
    query = update.callback_query
    if not await ensure_allowed(update, context):
        return ConversationHandler.END
    if not await ensure_private(update, context):
        return ConversationHandler.END
    if not await gate_callback(query, context):
        return ConversationHandler.END
    tok = query.data.split(":", 1)[1]
    user_id = query.from_user.id
    slug = resolve_token_to_slug(context, user_id, tok)
    if not slug:
        try:
            await query.edit_message_text(MESSAGES["TOO_LATE_UNDO"])
        except Exception:
            pass
        return ConversationHandler.END
    pending = get_pending_deletes_map(context, user_id)
    job = pending.get(slug)
    if job and getattr(job, "enabled", True):
        try:
            job.schedule_removal()
        except Exception:
            pass
        pending.pop(slug, None)
        try:
            await query.edit_message_text(MESSAGES["UNDO_DELETE_CANCELLED"].format(slug=slug))
        except Exception:
            pass
    else:
        pending.pop(slug, None)
        try:
            await query.edit_message_text(MESSAGES["TOO_LATE_UNDO"])
        except Exception:
            pass


async def confirm_delete_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await ensure_allowed(update, context):
        return ConversationHandler.END
    if not await ensure_private(update, context):
        return ConversationHandler.END
    if not await gate_callback(query, context):
        return CONFIRM_DELETE
    if query.data == CB_CANCEL:
        return await cancel(update, context)
    if query.data != CB_CONFIRM_DELETE:
        await query.answer(MESSAGES["INVALID_OR_STALE_ACTION"])  # best effort
        return CONFIRM_DELETE
    slug = context.user_data.get(K_SLUG)
    if not is_valid_slug(slug or ""):
        await query.edit_message_text(invalid_slug_text())
        return ConversationHandler.END
    await schedule_delete_with_undo(query, context, slug)
    return ConversationHandler.END


# ----- List Posts -----
async def fetch_posts_for_user(
    user_id: int, context: ContextTypes.DEFAULT_TYPE, *, chat_id: Optional[int] = None
) -> Optional[List[dict]]:
    api_key = users_data[user_id].api_key
    # Cache to reduce API calls
    cache = context.user_data.get(K_POSTS_CACHE)
    if cache and time.time() - cache.get("ts", 0) < POSTS_CACHE_TTL:
        return cache.get("posts")
    response, data = await api_call("GET", api_key, context=context, chat_id=chat_id)
    posts: Optional[List[dict]] = None
    if http_2xx(response):
        if isinstance(data, dict) and isinstance(data.get("post_list"), list):
            posts = data.get("post_list", [])
        elif isinstance(data, list):
            posts = data
    elif payload_ok(data) and isinstance(data, dict):
        if isinstance(data.get("post_list"), list):
            posts = data.get("post_list")
    if posts is not None:
        context.user_data[K_POSTS_CACHE] = {"ts": time.time(), "posts": posts}
        return posts
    return None


async def list_posts(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    filter_mode: str = "all",
    query: Optional[str] = None,
) -> None:
    """Render the paginated posts list for a user into the chat."""
    if not await ensure_allowed(update, context):
        return
    if not await ensure_private(update, context):
        return
    user_id = await ensure_api_key_or_prompt(update, context)
    if not user_id:
        return
    posts = await fetch_posts_for_user(user_id, context, chat_id=safe_chat_id(update))
    if posts is None:
        await update.message.reply_text(MESSAGES["FAILED_FETCH_POSTS"])
        return
    page = 1
    preview_len = get_effective_preview_length(user_id)
    text, markup, page_used = build_list_message(
        posts, filter_mode, page, query, preview_len, context, user_id
    )
    set_list_state(context, {"filter": filter_mode, "page": page_used, "query": query})
    await update.message.reply_text(
        text, reply_markup=markup, disable_web_page_preview=True, parse_mode="MarkdownV2"
    )


async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /list [published|drafts]
    if not await ensure_allowed(update, context):
        return
    if not await ensure_private(update, context):
        return
    user_id = await ensure_api_key_or_prompt(update, context)
    if not user_id:
        return
    arg = (context.args[0].lower() if context.args else "all")
    if arg not in ("all", "published", "drafts"):
        arg = "all"
    await list_posts(update, context, filter_mode=arg)


async def drafts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_allowed(update, context):
        return
    if not await ensure_private(update, context):
        return
    user_id = await ensure_api_key_or_prompt(update, context)
    if not user_id:
        return
    await list_posts(update, context, filter_mode="drafts")


async def published_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_allowed(update, context):
        return
    if not await ensure_private(update, context):
        return
    user_id = await ensure_api_key_or_prompt(update, context)
    if not user_id:
        return
    await list_posts(update, context, filter_mode="published")


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_allowed(update, context):
        return
    if not await ensure_private(update, context):
        return
    user_id = await ensure_api_key_or_prompt(update, context)
    if not user_id:
        return
    if not context.args:
        await update.message.reply_text(MESSAGES["USAGE_SEARCH"])
        return
    query = " ".join(context.args).strip()
    await list_posts(update, context, filter_mode="all", query=query)


async def list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle pagination/filter interactions in the posts list view."""
    query = update.callback_query
    if not await ensure_allowed(update, context):
        return
    if not await ensure_private(update, context):
        return
    if not await gate_callback(query, context):
        return
    # Ensure API key configured
    user_id = await ensure_api_key_or_prompt(update, context)
    if not user_id:
        return
    chat_id = safe_chat_id(query=query)
    posts = await fetch_posts_for_user(user_id, context, chat_id=chat_id)
    if posts is None:
        await query.edit_message_text(MESSAGES["FAILED_FETCH_POSTS"])
        return
    state = get_list_state(context)
    qd = query.data
    if qd.startswith(CB_LIST_FILTER_PREFIX):
        state["filter"] = qd[len(CB_LIST_FILTER_PREFIX):]
        state["page"] = 1
    elif qd.startswith(CB_LIST_PAGE_PREFIX):
        suffix = qd[len(CB_LIST_PAGE_PREFIX):]
        if suffix == "next":
            state["page"] = state.get("page", 1) + 1
        elif suffix == "prev":
            state["page"] = max(1, state.get("page", 1) - 1)
    elif qd == CB_LIST_REFRESH:
        # Clear cache
        context.user_data.pop(K_POSTS_CACHE, None)
        posts = await fetch_posts_for_user(user_id, context, chat_id=chat_id)
        if posts is None:
            await query.edit_message_text(MESSAGES["FAILED_FETCH_POSTS"])
            return
    elif qd == CB_LIST_NOP:
        pass
    elif qd.startswith(f"{CB_LIST_PREFIX}manage:"):
        tok = qd.split(":", 2)[2] if ":" in qd else ""
        slug = resolve_token_to_slug(context, user_id, tok) if tok else None
        if not slug or not is_valid_slug(slug):
            await query.edit_message_text(invalid_slug_text())
            return
        # Build manage view for this slug
        post = next((p for p in posts if p.get("slug") == slug), None)
        title = (post or {}).get("title", "No Title")
        url = (post or {}).get("url", "")
        is_pub = bool((post or {}).get("published_at"))
        status = "Published" if is_pub else "Draft"
        text = (
            f"*🛠 Manage Post*\n\n"
            f"*Title:*\n{mdv2(title)}\n\n"
            f"*Slug:*\n{mdv2(slug)}\n\n"
            f"*Status:*\n{mdv2(status)}"
        )
        rows: List[List[InlineKeyboardButton]] = []
        tok2 = token_for_slug(context, user_id, slug)
        rows.append([
            InlineKeyboardButton(MESSAGES["BUTTON_EDIT"], callback_data=f"{CB_EDIT_PREFIX}{tok2}"),
            InlineKeyboardButton(MESSAGES["BUTTON_DELETE"], callback_data=f"{CB_DELETE_PREFIX}{tok2}"),
        ])
        toggle_label = "Unpublish" if is_pub else "Publish"
        row2: List[InlineKeyboardButton] = [InlineKeyboardButton(toggle_label, callback_data=f"{CB_TOGGLEPUB_PREFIX}{tok2}")]
        if is_valid_absolute_url(url):
            row2.append(open_url_button(url))
        rows.append(row2)
        rows.append([InlineKeyboardButton("Back to list", callback_data=f"{CB_LIST_PREFIX}back")])
        await query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(rows), disable_web_page_preview=True)
        return
    elif qd == f"{CB_LIST_PREFIX}back":
        # Just re-render list using existing state
        pass
    else:
        # Unknown within list: ignore
        pass
    preview_len = get_effective_preview_length(user_id)
    text, markup, page_used = build_list_message(
        posts, state.get("filter", "all"), state.get("page", 1), state.get("query"), preview_len, context, user_id
    )
    state["page"] = page_used  # sync clamped page back to state
    set_list_state(context, state)
    await query.edit_message_text(
        text, reply_markup=markup, disable_web_page_preview=True, parse_mode="MarkdownV2"
    )


async def toggle_publish_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await ensure_allowed(update, context):
        return
    if not await ensure_private(update, context):
        return
    if not await gate_callback(query, context):
        return
    # Ensure API key configured
    user_id = await ensure_api_key_or_prompt(update, context)
    if not user_id:
        return
    api_key = users_data[user_id].api_key
    tok = query.data.split(":", 1)[1]
    slug = resolve_token_to_slug(context, user_id, tok)
    if not slug or not is_valid_slug(slug):
        await query.edit_message_text(invalid_slug_text())
        return
    # Determine current status
    chat_id = safe_chat_id(query=query)
    if chat_id is not None:
        await send_typing(context, chat_id)
    response, data = await api_call("GET", api_key, slug=slug, context=context, chat_id=chat_id)
    ok_shape = isinstance(data, dict) and ("title" in data or "body" in data or "slug" in data)
    if not ((http_2xx(response) and ok_shape) or payload_ok(data)):
        await query.edit_message_text(MESSAGES["FAILED_FETCH_FOR_TOGGLE"])
        return
    is_pub = bool(data.get("published_at")) if isinstance(data, dict) else False
    payload = {"published_at": None if is_pub else now_date_str()}

    # Save for retry
    users_data[user_id].last_action = {"type": "togglepub", "slug": slug, "payload": payload}
    await save_users_data()

    response2, data2 = await api_call(
        "PATCH", api_key, slug=slug, payload=payload, context=context, chat_id=chat_id
    )
    if http_2xx(response2) or payload_ok(data2):
        # Refresh list view inline without tripping cooldown
        context.user_data.pop(K_POSTS_CACHE, None)
        state = get_list_state(context)
        posts = await fetch_posts_for_user(user_id, context, chat_id=chat_id)
        if posts is not None:
            preview_len = get_effective_preview_length(user_id)
            text, markup, page_used = build_list_message(
                posts, state.get("filter", "all"), state.get("page", 1), state.get("query"), preview_len, context, user_id
            )
            state["page"] = page_used
            set_list_state(context, state)
            await query.edit_message_text(
                text, reply_markup=markup, disable_web_page_preview=True, parse_mode="MarkdownV2"
            )
        else:
            try:
                await query.edit_message_text(MESSAGES["TOGGLED_PUBLISH_STATE"])
            except Exception:
                pass
        # Clear last action
        try:
            users_data[user_id].last_action = {}
            await save_users_data()
        except Exception:
            pass
    else:
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(MESSAGES["BUTTON_RETRY"], callback_data=CB_RETRY_TOGGLEPUB)]]
        )
        try:
            await query.edit_message_text(MESSAGES["FAILED_TOGGLE_PUBLISH"], reply_markup=keyboard)
        except Exception:
            pass


# ----- Settings -----

def build_settings_text(user_id: int) -> str:
    u = users_data[user_id]
    return (
        f"{MESSAGES['SETTINGS_HEADER']}\n"
        f"- Default publish mode: {u.settings.get('default_publish_mode', 'draft')}\n"
        f"- Preview length: {u.settings.get('preview_length', DEFAULT_PREVIEW_LENGTH)}\n"
        f"- Confirm before delete: {u.settings.get('confirm_before_delete', True)}\n"
    )


def build_settings_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(MESSAGES["BUTTON_TOGGLE_DEFAULT_MODE"], callback_data=CB_SETTINGS_MODE)],
            [
                InlineKeyboardButton("Preview 140", callback_data=f"{CB_SETTINGS_PREV_PREFIX}140"),
                InlineKeyboardButton("280", callback_data=f"{CB_SETTINGS_PREV_PREFIX}280"),
                InlineKeyboardButton("500", callback_data=f"{CB_SETTINGS_PREV_PREFIX}500"),
            ],
            [InlineKeyboardButton(MESSAGES["BUTTON_TOGGLE_CONFIRM_DELETE"], callback_data=CB_SETTINGS_CONFIRM)],
        ]
    )


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_allowed(update, context):
        return
    if not await ensure_private(update, context):
        return
    user_id = await ensure_api_key_or_prompt(update, context)
    if not user_id:
        return
    await update.message.reply_text(build_settings_text(user_id), reply_markup=build_settings_keyboard(user_id))


async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await ensure_allowed(update, context):
        return
    if not await ensure_private(update, context):
        return
    if not await gate_callback(query, context):
        return
    # Ensure API key configured
    user_id = await ensure_api_key_or_prompt(update, context)
    if not user_id:
        return
    u = users_data[user_id]
    parts = query.data.split(":")
    if len(parts) >= 2 and parts[1] == "mode":
        u.settings["default_publish_mode"] = (
            "publish" if u.settings.get("default_publish_mode") == "draft" else "draft"
        )
    elif len(parts) >= 3 and parts[1] == "prev":
        try:
            val = int(parts[2])
            if val in ALLOWED_PREVIEW_LENGTHS:
                u.settings["preview_length"] = val
        except Exception:
            pass
    elif len(parts) >= 2 and parts[1] == "confirm":
        u.settings["confirm_before_delete"] = not u.settings.get(
            "confirm_before_delete", True
        )
    await save_users_data()
    # Re-render
    try:
        await query.edit_message_text(build_settings_text(user_id), reply_markup=build_settings_keyboard(user_id))
    except Exception:
        pass


# ----- Status -----
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_allowed(update, context):
        return
    if not await ensure_private(update, context):
        return
    user_id = await ensure_api_key_or_prompt(update, context)
    if not user_id:
        return
    api_key = users_data[user_id].api_key
    t1 = time.time()
    response, _ = await api_call("GET", api_key, context=context, chat_id=safe_chat_id(update))
    t2 = time.time()
    if http_2xx(response):
        await update.message.reply_text(MESSAGES["STATUS_API_REACHABLE"].format(ms=int((t2 - t1)*1000)))
    else:
        await update.message.reply_text(MESSAGES["STATUS_API_UNREACHABLE"])


# ----- Retry Handler -----
async def retry_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await ensure_allowed(update, context):
        return
    if not await ensure_private(update, context):
        return
    if not await gate_callback(query, context):
        return
    user_id = await ensure_api_key_or_prompt(update, context)
    if not user_id:
        return
    last = users_data.get(user_id).last_action if users_data.get(user_id) else None
    if not last:
        await query.edit_message_text(MESSAGES["NO_ACTION_TO_RETRY"])
        return
    t = last.get("type")
    api_key = users_data[user_id].api_key
    chat_id = safe_chat_id(query=query)
    if t == "create":
        # Reuse context.user_data fields
        context.user_data[K_TITLE] = last.get("payload", {}).get(
            "title", context.user_data.get(K_TITLE)
        )
        context.user_data[K_BODY] = last.get("payload", {}).get(
            "body", context.user_data.get(K_BODY)
        )
        context.user_data[K_PUBLISHED_AT] = last.get("payload", {}).get(
            "published_at", context.user_data.get(K_PUBLISHED_AT)
        )
        await _submit_create_post(query, context)
    elif t == "update":
        context.user_data[K_PUBLISHED_AT] = last.get("payload", {}).get("published_at")
        context.user_data[K_TITLE] = last.get("payload", {}).get("title")
        context.user_data[K_BODY] = last.get("payload", {}).get("body")
        context.user_data[K_FINAL_SLUG] = last.get("payload", {}).get("slug", last.get("slug"))
        context.user_data[K_CURRENT_SLUG] = last.get("slug")
        await _submit_update_post(query, context)
    elif t == "togglepub":
        slug = last.get("slug")
        payload = last.get("payload", {})
        if not is_valid_slug(slug or ""):
            await query.edit_message_text(invalid_slug_text())
            return
        response, data = await api_call(
            "PATCH", api_key, slug=slug, payload=payload, context=context, chat_id=chat_id
        )
        if http_2xx(response) or payload_ok(data):
            # Refresh list if possible
            context.user_data.pop(K_POSTS_CACHE, None)
            state = get_list_state(context)
            posts = await fetch_posts_for_user(user_id, context, chat_id=chat_id)
            if posts is not None:
                preview_len = get_effective_preview_length(user_id)
                text, markup, page_used = build_list_message(
                    posts,
                    state.get("filter", "all"),
                    state.get("page", 1),
                    state.get("query"),
                    preview_len,
                    context,
                    user_id,
                )
                state["page"] = page_used
                set_list_state(context, state)
                await query.edit_message_text(
                    text, reply_markup=markup, disable_web_page_preview=True, parse_mode="MarkdownV2"
                )
            else:
                await query.edit_message_text(MESSAGES["TOGGLED_PUBLISH_STATE"])
            try:
                users_data[user_id].last_action = {}
                await save_users_data()
            except Exception:
                pass
        else:
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton(MESSAGES["BUTTON_RETRY"], callback_data=CB_RETRY_TOGGLEPUB)]]
            )
            await query.edit_message_text(
                MESSAGES["FAILED_TOGGLE_PUBLISH"], reply_markup=keyboard
            )
    elif t == "delete":
        slug = last.get("slug")
        if not is_valid_slug(slug or ""):
            await query.edit_message_text(invalid_slug_text())
            return
        response, _ = await api_call(
            "DELETE", api_key, slug=slug, context=context, chat_id=chat_id
        )
        if http_2xx(response):
            await query.edit_message_text(MESSAGES["POST_DELETED"].format(slug=slug))
            try:
                users_data[user_id].last_action = {}
                await save_users_data()
            except Exception:
                pass
        else:
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton(MESSAGES["BUTTON_RETRY"], callback_data=CB_RETRY_DELETE)]]
            )
            await query.edit_message_text(
                MESSAGES["FAILED_DELETE_POST_SIMPLE"], reply_markup=keyboard
            )
    else:
        await query.edit_message_text(MESSAGES["UNSUPPORTED_RETRY"])


# ----- Global Error Handler -----
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = None
        if isinstance(update, Update) and update.effective_user:
            uid = update.effective_user.id
        logger.error("Error for user %s: %s", uid, getattr(context, "error", None))
    except Exception:
        logger.error("Unhandled error: %s", getattr(context, "error", None))


# ---------- Main Function ----------
def main():
    import asyncio

    # Load .env if available
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv()
    except Exception:
        pass

    # Reload allowlist from environment after .env load
    reload_env_allowlist()

    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN environment variable is required.")

    # Enforce secure permissions before any file I/O
    enforce_config_permissions()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(load_users_data())

    # Log allowlist state for clarity
    if not effective_allowlist():
        logger.warning(
            "ALLOWLIST is empty; bot will accept commands from any Telegram user."
        )

    application = Application.builder().token(token).build()

    conv_start = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ENTER_API_KEY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_api_key)
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(cancel, pattern=f"^{CB_CANCEL}$"),
        ],
    )

    conv_post = ConversationHandler(
        entry_points=[CommandHandler("post", post), CommandHandler("new", new_command)],
        states={
            ENTER_TITLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_title)
            ],
            ENTER_BODY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_body),
                CommandHandler("done", draft_done),
                CommandHandler("preview", draft_preview),
                CommandHandler("clear", draft_clear),
                CallbackQueryHandler(draft_done_cb, pattern=f"^{CB_DRAFT_DONE}$"),
                CallbackQueryHandler(draft_preview_cb, pattern=f"^{CB_DRAFT_PREVIEW}$"),
                CallbackQueryHandler(draft_clear_cb, pattern=f"^{CB_DRAFT_CLEAR}$"),
                CallbackQueryHandler(draft_undo_cb, pattern=f"^{CB_DRAFT_UNDO}$"),
                CallbackQueryHandler(template_insert_cb, pattern=f"^{CB_TMPL_PREFIX}"),
                CallbackQueryHandler(cancel, pattern=f"^{CB_CANCEL}$"),
            ],
            ENTER_PUBLISH_CHOICE: [
                CallbackQueryHandler(
                    post_publish_choice, pattern=f"^({CB_CHOICE_DRAFT}|{CB_CHOICE_PUBLISH})$"
                )
            ],
            CONFIRM_POST: [
                CallbackQueryHandler(
                    confirm_post_handler, pattern=f"^{CB_SUBMIT_POST}$"
                )
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(cancel, pattern=f"^{CB_CANCEL}$"),
        ],
    )

    conv_update = ConversationHandler(
        entry_points=[
            CommandHandler("update", update_command),
            CallbackQueryHandler(inline_edit_start, pattern=f"^{CB_EDIT_PREFIX}"),
        ],
        states={
            ENTER_UPDATE_SLUG: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_update_slug)
            ],
            ENTER_UPDATED_TITLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_updated_title)
            ],
            ENTER_UPDATED_BODY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_updated_body)
            ],
            ENTER_PUBLISH_CHOICE_UPDATE: [
                CallbackQueryHandler(
                    update_publish_choice, pattern=f"^({CB_CHOICE_DRAFT}|{CB_CHOICE_PUBLISH})$"
                )
            ],
            CONFIRM_UPDATE: [
                CallbackQueryHandler(toggle_slug_sync, pattern=f"^{CB_SLUGSYNC}$"),
                CallbackQueryHandler(
                    confirm_update_handler, pattern=f"^{CB_SUBMIT_UPDATE}$"
                ),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(cancel, pattern=f"^{CB_CANCEL}$"),
        ],
    )

    conv_delete = ConversationHandler(
        entry_points=[
            CommandHandler("delete", delete_command),
            CallbackQueryHandler(inline_delete_start, pattern=f"^{CB_DELETE_PREFIX}"),
        ],
        states={
            ENTER_DELETE_SLUG: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, enter_delete_slug)
            ],
            CONFIRM_DELETE: [
                CallbackQueryHandler(
                    confirm_delete_handler, pattern=f"^{CB_CONFIRM_DELETE}$"
                )
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(cancel, pattern=f"^{CB_CANCEL}$"),
        ],
    )

    application.add_handler(conv_start)
    application.add_handler(conv_post)
    application.add_handler(conv_update)
    application.add_handler(conv_delete)

    application.add_handler(CommandHandler("list", list_command))
    application.add_handler(CommandHandler("drafts", drafts_command))
    application.add_handler(CommandHandler("published", published_command))
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("status", status_command))

    # Callback handlers outside conversations
    application.add_handler(CallbackQueryHandler(list_callback, pattern=f"^{CB_LIST_PREFIX}"))
    application.add_handler(CallbackQueryHandler(toggle_publish_handler, pattern=f"^{CB_TOGGLEPUB_PREFIX}"))
    application.add_handler(CallbackQueryHandler(settings_callback, pattern=f"^{CB_SETTINGS_PREFIX}"))
    application.add_handler(CallbackQueryHandler(undo_delete_handler, pattern=f"^{CB_UNDO_DELETE_PREFIX}"))
    application.add_handler(CallbackQueryHandler(retry_handler, pattern=f"^{CB_RETRY_PREFIX}"))

    application.add_error_handler(error_handler)

    application.run_polling()


if __name__ == "__main__":
    main()
