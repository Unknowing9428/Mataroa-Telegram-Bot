import json
import os
import logging
import sys
from datetime import datetime
from dataclasses import dataclass

import httpx
import aiofiles
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
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
sys.stdout = sys.stderr

# Conversation states
(ENTER_API_KEY, ENTER_TITLE, ENTER_BODY, ENTER_PUBLISH_CHOICE,
 ENTER_DELETE_SLUG, ENTER_UPDATE_SLUG, ENTER_UPDATED_TITLE,
 ENTER_UPDATED_BODY, ENTER_PUBLISH_CHOICE_UPDATE, CONFIRM_POST,
 CONFIRM_UPDATE, CONFIRM_DELETE) = range(12)

# API and bot settings
API_URL = "https://mataroa.blog/api/posts/"
TOKEN = "bot_token_here"
USERS_JSON_PATH = "users.json"
users_data = {}


@dataclass
class UserData:
    api_key: str
    title: str = ""
    body: str = ""
    published_at: str = None


# ---------- Helper: Cancel Inline Keyboard ----------
def cancel_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="cancel")]])


# ---------- Async File I/O Helpers ----------
async def load_users_data():
    global users_data
    if os.path.exists(USERS_JSON_PATH):
        async with aiofiles.open(USERS_JSON_PATH, "r") as f:
            data = await f.read()
            json_data = json.loads(data)
            users_data = {int(k): UserData(**v) for k, v in json_data.items()}
    else:
        users_data = {}


async def save_users_data():
    async with aiofiles.open(USERS_JSON_PATH, "w") as f:
        await f.write(json.dumps({k: v.__dict__ for k, v in users_data.items()}))


# ---------- API Helper ----------
async def api_call(method: str, api_key: str, slug: str = None, payload: dict = None):
    url = API_URL if slug is None else f"{API_URL}{slug}/"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.request(method, url, headers=headers, json=payload, timeout=10.0)
        return response, response.json()
    except Exception as e:
        logger.error("API call error: %s", e)
        return None, None


# ---------- Command Handlers ----------

# /start: Set API key
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to the Mataroa.blog bot! Please enter your API key.",
        reply_markup=cancel_keyboard()
    )
    return ENTER_API_KEY


async def enter_api_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    api_key = update.message.text.strip()
    users_data[user_id] = UserData(api_key=api_key)
    await save_users_data()
    await update.message.reply_text("✅ API key saved! Use /post, /update, /delete, /list or /help.")
    return ConversationHandler.END


# /help: Show help information
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🤖 *Mataroa.blog Bot Help*\n\n"
        "/start - Set your API key\n"
        "/post - Create a new post\n"
        "/update - Update an existing post\n"
        "/delete - Delete a post\n"
        "/list - List your posts\n"
        "/help - Show this help message\n\n"
        "To update a post, type /update and then enter the post's slug when prompted.\n"
        "To delete a post, type /delete and then enter the post's slug when prompted."
    )
    await update.message.reply_markdown(help_text)


# Global cancel handler (for messages & inline keyboards)
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("Operation cancelled.")
    else:
        await update.message.reply_text("Operation cancelled.")
    return ConversationHandler.END


# ----- New Post Flow -----
async def post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id not in users_data:
        await update.message.reply_text("🔑 Set your API key first using /start.")
        return ConversationHandler.END
    await update.message.reply_text("📝 Enter the title of your post:", reply_markup=cancel_keyboard())
    return ENTER_TITLE


async def enter_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = update.message.text.strip()
    if not title:
        await update.message.reply_text("Please provide a valid title:", reply_markup=cancel_keyboard())
        return ENTER_TITLE
    context.user_data["title"] = title
    await update.message.reply_text("✏️ Enter the body/content of your post:", reply_markup=cancel_keyboard())
    return ENTER_BODY


async def enter_body(update: Update, context: ContextTypes.DEFAULT_TYPE):
    body = update.message.text.strip()
    if not body:
        await update.message.reply_text("Please provide valid content:", reply_markup=cancel_keyboard())
        return ENTER_BODY
    context.user_data["body"] = body
    keyboard = [
        [InlineKeyboardButton("Save as Draft", callback_data="draft"),
         InlineKeyboardButton("Publish Now", callback_data="publish")],
        [InlineKeyboardButton("Cancel", callback_data="cancel")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Choose publication option:", reply_markup=reply_markup)
    return ENTER_PUBLISH_CHOICE


async def post_publish_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "cancel":
        return await cancel(update, context)
    choice = query.data
    published_at = None if choice == "draft" else datetime.now().strftime("%Y-%m-%d")
    context.user_data["published_at"] = published_at
    # Show preview with confirmation options
    preview = (
        f"*Preview Post:*\n\n"
        f"*Title:*\n{context.user_data['title']}\n\n"
        f"*Body:*\n{context.user_data['body']}\n\n"
        f"*Status:*\n{'Draft' if published_at is None else 'Published'}"
    )
    keyboard = [
        [InlineKeyboardButton("Submit", callback_data="submit_post"),
         InlineKeyboardButton("Cancel", callback_data="cancel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(preview, parse_mode="Markdown", reply_markup=reply_markup)
    return CONFIRM_POST


async def confirm_post_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "cancel":
        return await cancel(update, context)
    user_id = query.from_user.id
    api_key = users_data[user_id].api_key
    payload = {
        "title": context.user_data["title"],
        "body": context.user_data["body"],
        "published_at": context.user_data["published_at"],
    }
    response, data = await api_call("POST", api_key, payload=payload)
    if response and response.status_code in (200, 201) and data.get("ok"):
        slug = data.get("slug")
        url = data.get("url")
        # Add inline buttons for immediate Edit and Delete actions.
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Edit", callback_data=f"edit:{slug}"),
              InlineKeyboardButton("Delete", callback_data=f"delete:{slug}")]]
        )
        await query.edit_message_text(f"✅ Post created!\nURL: {url}", reply_markup=keyboard)
    else:
        await query.edit_message_text("❌ Failed to create post. Try again later.")
    return ConversationHandler.END


# ----- Update Post Flow -----
async def update_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id not in users_data:
        await update.message.reply_text("🔑 Set your API key first using /start.")
        return ConversationHandler.END
    await update.message.reply_text("✏️ Enter the slug of the post to update:", reply_markup=cancel_keyboard())
    return ENTER_UPDATE_SLUG


# New entry point for inline edit button.
async def inline_edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    slug = query.data.split("edit:", 1)[1]
    context.user_data["slug"] = slug
    user_id = query.from_user.id
    api_key = users_data[user_id].api_key
    response, res_data = await api_call("GET", api_key, slug=slug)
    if response and response.status_code == 200 and res_data.get("ok"):
        current_title = res_data.get("title", "N/A")
        current_body = res_data.get("body", "N/A")
        message = (
            f"*Current Title:*\n{current_title}\n\n"
            f"*Current Body:*\n{current_body}\n\n"
            "Enter the updated title:"
        )
        await query.edit_message_text(message, parse_mode="Markdown", reply_markup=cancel_keyboard())
        return ENTER_UPDATED_TITLE
    else:
        await query.edit_message_text("❌ Failed to fetch post details.")
        return ConversationHandler.END


async def enter_update_slug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    slug = update.message.text.strip()
    if not slug:
        await update.message.reply_text("Please enter a valid slug:", reply_markup=cancel_keyboard())
        return ENTER_UPDATE_SLUG
    context.user_data["slug"] = slug
    user_id = update.message.from_user.id
    api_key = users_data[user_id].api_key
    response, data = await api_call("GET", api_key, slug=slug)
    if response and response.status_code == 200 and data.get("ok"):
        current_title = data.get("title", "N/A")
        current_body = data.get("body", "N/A")
        message = (
            f"*Current Title:*\n{current_title}\n\n"
            f"*Current Body:*\n{current_body}\n\n"
            "Enter the updated title:"
        )
        await update.message.reply_text(message, parse_mode="Markdown", reply_markup=cancel_keyboard())
        return ENTER_UPDATED_TITLE
    else:
        await update.message.reply_text("❌ Failed to fetch post details. Check the slug and try again.")
        return ConversationHandler.END


async def enter_updated_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    updated_title = update.message.text.strip()
    if not updated_title:
        await update.message.reply_text("Please provide a valid title:", reply_markup=cancel_keyboard())
        return ENTER_UPDATED_TITLE
    context.user_data["title"] = updated_title
    await update.message.reply_text("Enter the updated body/content:", reply_markup=cancel_keyboard())
    return ENTER_UPDATED_BODY


async def enter_updated_body(update: Update, context: ContextTypes.DEFAULT_TYPE):
    updated_body = update.message.text.strip()
    if not updated_body:
        await update.message.reply_text("Please provide valid content:", reply_markup=cancel_keyboard())
        return ENTER_UPDATED_BODY
    context.user_data["body"] = updated_body
    keyboard = [
        [InlineKeyboardButton("Save as Draft", callback_data="draft"),
         InlineKeyboardButton("Publish Now", callback_data="publish")],
        [InlineKeyboardButton("Cancel", callback_data="cancel")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Choose publication option:", reply_markup=reply_markup)
    return ENTER_PUBLISH_CHOICE_UPDATE


async def update_publish_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "cancel":
        return await cancel(update, context)
    choice = query.data
    published_at = None if choice == "draft" else datetime.now().strftime("%Y-%m-%d")
    context.user_data["published_at"] = published_at
    preview = (
        f"*Preview Updated Post:*\n\n"
        f"*Title:*\n{context.user_data['title']}\n\n"
        f"*Body:*\n{context.user_data['body']}\n\n"
        f"*Status:*\n{'Draft' if published_at is None else 'Published'}"
    )
    keyboard = [
        [InlineKeyboardButton("Submit", callback_data="submit_update"),
         InlineKeyboardButton("Cancel", callback_data="cancel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(preview, parse_mode="Markdown", reply_markup=reply_markup)
    return CONFIRM_UPDATE


async def confirm_update_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "cancel":
        return await cancel(update, context)
    user_id = query.from_user.id
    api_key = users_data[user_id].api_key
    slug = context.user_data.get("slug")
    payload = {
        "title": context.user_data["title"],
        "body": context.user_data["body"],
        "published_at": context.user_data["published_at"],
    }
    response, data = await api_call("PATCH", api_key, slug=slug, payload=payload)
    if response and response.status_code in (200, 201) and data.get("ok"):
        url = data.get("url")
        # Include inline Edit and Delete buttons after update confirmation.
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Edit", callback_data=f"edit:{slug}"),
              InlineKeyboardButton("Delete", callback_data=f"delete:{slug}")]]
        )
        await query.edit_message_text(f"✅ Post updated!\nURL: {url}", reply_markup=keyboard)
    else:
        await query.edit_message_text("❌ Failed to update post. Try again later.")
    return ConversationHandler.END


# ----- Delete Post Flow -----
async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id not in users_data:
        await update.message.reply_text("🔑 Set your API key first using /start.")
        return ConversationHandler.END
    if context.args:
        context.user_data["slug"] = context.args[0]
        return await confirm_delete_prompt(update, context)
    await update.message.reply_text("✏️ Enter the slug of the post you want to delete:", reply_markup=cancel_keyboard())
    return ENTER_DELETE_SLUG


# New entry point for inline delete button.
async def inline_delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    slug = query.data.split("delete:", 1)[1]
    context.user_data["slug"] = slug
    return await confirm_delete_prompt(update, context)


async def enter_delete_slug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    slug = update.message.text.strip()
    if not slug:
        await update.message.reply_text("Please enter a valid slug:", reply_markup=cancel_keyboard())
        return ENTER_DELETE_SLUG
    context.user_data["slug"] = slug
    return await confirm_delete_prompt(update, context)


async def confirm_delete_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Yes, Delete", callback_data="confirm_delete"),
         InlineKeyboardButton("Cancel", callback_data="cancel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.message:
        await update.message.reply_text(f"⚠️ Are you sure you want to delete post '{context.user_data['slug']}'?",
                                          reply_markup=reply_markup)
    elif update.callback_query:
        await update.callback_query.edit_message_text(
            f"⚠️ Are you sure you want to delete post '{context.user_data['slug']}'?", reply_markup=reply_markup
        )
    return CONFIRM_DELETE


async def confirm_delete_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "cancel":
        return await cancel(update, context)
    user_id = query.from_user.id
    api_key = users_data[user_id].api_key
    slug = context.user_data.get("slug")
    response, data = await api_call("DELETE", api_key, slug=slug)
    if response and response.status_code == 200 and data.get("ok"):
        await query.edit_message_text(f"✅ Post with slug '{slug}' deleted.")
    else:
        await query.edit_message_text("❌ Failed to delete post. Check the slug and try again.")
    return ConversationHandler.END


# ----- List Posts -----
async def list_posts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id not in users_data:
        await update.message.reply_text("🔑 Set your API key first using /start.")
        return
    api_key = users_data[user_id].api_key
    response, data = await api_call("GET", api_key)
    if response and response.status_code == 200 and data.get("ok"):
        posts = data.get("post_list", [])
        if not posts:
            await update.message.reply_text("📭 You have no posts on Mataroa.blog.")
        else:
            message = "📄 *Your Posts:*\n"
            for post in posts:
                title = post.get("title", "No Title")
                slug = post.get("slug", "")
                url = post.get("url", "#")
                message += f"\n🔗 [{title}]({url})\n- `{slug}`\n"
            message += (
                "\nTo update a post, type /update and then enter the post's slug when prompted."
                "\nTo delete a post, type /delete and then enter the post's slug when prompted."
            )
            await update.message.reply_markdown(message)
    else:
        await update.message.reply_text("❌ Failed to fetch posts. Try again later.")


# ----- Global Error Handler -----
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Update %s caused error %s", update, context.error)


# ---------- Main Function ----------
def main():
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(load_users_data())

    application = Application.builder().token(TOKEN).build()

    conv_start = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ENTER_API_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_api_key)],
        },
        fallbacks=[CommandHandler("cancel", cancel),
                   CallbackQueryHandler(cancel, pattern="^cancel$")],
    )

    conv_post = ConversationHandler(
        entry_points=[CommandHandler("post", post)],
        states={
            ENTER_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_title)],
            ENTER_BODY: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_body)],
            ENTER_PUBLISH_CHOICE: [CallbackQueryHandler(post_publish_choice)],
            CONFIRM_POST: [CallbackQueryHandler(confirm_post_handler)],
        },
        fallbacks=[CommandHandler("cancel", cancel),
                   CallbackQueryHandler(cancel, pattern="^cancel$")],
    )

    conv_update = ConversationHandler(
        entry_points=[
            CommandHandler("update", update_command),
            CallbackQueryHandler(inline_edit_start, pattern="^edit:")
        ],
        states={
            ENTER_UPDATE_SLUG: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_update_slug)],
            ENTER_UPDATED_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_updated_title)],
            ENTER_UPDATED_BODY: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_updated_body)],
            ENTER_PUBLISH_CHOICE_UPDATE: [CallbackQueryHandler(update_publish_choice)],
            CONFIRM_UPDATE: [CallbackQueryHandler(confirm_update_handler)],
        },
        fallbacks=[CommandHandler("cancel", cancel),
                   CallbackQueryHandler(cancel, pattern="^cancel$")],
    )

    conv_delete = ConversationHandler(
        entry_points=[
            CommandHandler("delete", delete_command),
            CallbackQueryHandler(inline_delete_start, pattern="^delete:")
        ],
        states={
            ENTER_DELETE_SLUG: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_delete_slug)],
            CONFIRM_DELETE: [CallbackQueryHandler(confirm_delete_handler)],
        },
        fallbacks=[CommandHandler("cancel", cancel),
                   CallbackQueryHandler(cancel, pattern="^cancel$")],
    )

    application.add_handler(conv_start)
    application.add_handler(conv_post)
    application.add_handler(conv_update)
    application.add_handler(conv_delete)
    application.add_handler(CommandHandler("list", list_posts))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_error_handler(error_handler)

    application.run_polling()


if __name__ == "__main__":
    main()
