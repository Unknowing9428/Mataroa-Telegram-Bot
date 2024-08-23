import json
import os
import requests
import logging
import sys
import re
from datetime import datetime
from dataclasses import dataclass
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ConversationHandler, filters, CallbackQueryHandler
)
from telegram.ext import ContextTypes

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.WARNING
)

logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)
sys.stdout = sys.stderr

ENTER_API_KEY = 0
ENTER_TITLE = 1
ENTER_BODY = 2
ENTER_PUBLISH_CHOICE = 3
ENTER_DELETE_SLUG = 4
ENTER_UPDATE_SLUG = 5
ENTER_UPDATED_TITLE = 6
ENTER_UPDATED_BODY = 7
ENTER_PUBLISH_CHOICE_UPDATE = 8

API_URL = "https://mataroa.blog/api/posts/"

TOKEN = "bot_token_here"

USERS_JSON_PATH = "users.json"

@dataclass
class UserData:
    api_key: str
    title: str = ''
    body: str = ''
    published_at: str = None

if os.path.exists(USERS_JSON_PATH):
    with open(USERS_JSON_PATH, "r") as user_file:
        users_data = {int(k): UserData(**v) for k, v in json.load(user_file).items()}
else:
    users_data = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to the Mataroa.blog bot! To get started, please enter your Mataroa.blog API key.\n\n"
        "Don't worry, you only need to do this once. After that, you can create, update, or manage your posts directly from here."
    )
    return ENTER_API_KEY

async def enter_api_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    api_key = update.message.text.strip()

    logger.info(f"Received API key '{api_key}' from user {user_id}")

    users_data[user_id] = UserData(api_key=api_key)

    try:
        with open(USERS_JSON_PATH, "w") as user_file:
            json.dump({k: v.__dict__ for k, v in users_data.items()}, user_file)

        await update.message.reply_text(
            "✅ Your Mataroa.blog API key has been successfully updated! 🎉\n\n"
            "You can now start creating or managing your blog posts. Use /post to create a new post, /update to modify an existing one, or /delete to remove a post. If you need to see a list of your posts, try /list."
        )
    except Exception as e:
        await update.message.reply_text("❌ An error occurred while updating your API key. Please try again later.")
        logger.error(str(e))

    return ConversationHandler.END

async def post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.message.from_user.id
        if user_id not in users_data:
            await update.message.reply_text("🔑 Please enter your Mataroa.blog API key first using the /start command.")
            return
        
        logger.info(f"User {user_id} with API key '{users_data[user_id].api_key}' is creating a new blog post.")
        await update.message.reply_text("📝 Let's get started with your new blog post! Please enter the title of your post.")
        return ENTER_TITLE
    except Exception as e:
        await update.message.reply_text("❌ An error occurred. Please try again later.")
        logger.error(str(e))
        return ConversationHandler.END

async def enter_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        title = update.message.text.strip()
        logger.info(f"Received title '{title}' from user {update.message.from_user.id}")

        if not title:
            await update.message.reply_text("Please enter a valid title for your blog post.")
            return ENTER_TITLE

        context.user_data['title'] = title
        await update.message.reply_text("✏️ Now, let's add some content. Please enter the body of your blog post.")
        return ENTER_BODY
    except Exception as e:
        await update.message.reply_text("❌ An error occurred. Please try again later.")
        logger.error(str(e))
        return ConversationHandler.END

async def enter_body(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        body = update.message.text.strip()
        logger.info(f"Received body/content '{body}' from user {update.message.from_user.id}")
        if not body:
            await update.message.reply_text("✏️ Please enter a valid body/content for your blog post.")
            return ENTER_BODY
        
        context.user_data['body'] = body
        keyboard = [
            [InlineKeyboardButton("Save as Draft", callback_data='draft')],
            [InlineKeyboardButton("Publish Now", callback_data='publish')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "Almost done! How would you like to proceed?\n\n"
            "👉 Save as Draft: Keep it private for now.\n"
            "👉 Publish Now: Share it with the world immediately.",
            reply_markup=reply_markup
        )
        return ENTER_PUBLISH_CHOICE
    except Exception as e:
        await update.message.reply_text("❌ An error occurred. Please try again later.")
        logger.error(str(e))
        return ConversationHandler.END

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_choice = query.data
    user_id = query.from_user.id
    context.user_data['published_at'] = None if user_choice == 'draft' else datetime.now().strftime("%Y-%m-%d")

    api_key = users_data[user_id].api_key
    title = context.user_data['title']
    body = context.user_data['body']
    slug = context.user_data.get('slug')

    post_data = {
        "title": title,
        "body": body,
        "published_at": context.user_data['published_at']
    }

    if slug:
        post_data["slug"] = slug

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    try:
        if slug:
            response = requests.patch(f"{API_URL}{slug}/", json=post_data, headers=headers)
        else:
            response = requests.post(API_URL, json=post_data, headers=headers)

        response_data = response.json()

        if response.status_code in (200, 201) and response_data.get("ok"):
            if slug:
                await query.edit_message_text(
                    f"✅ Your blog post '{title}' has been updated! 🛠️\n\n"
                    f"You can view the updated post here: {response_data['url']}\n\n"
                    "Want to keep writing? Use /post to create something new. Use /list to show a list of posts or /update to edit a post."
                )
            else:
                await query.edit_message_text(
                    f"✅ Your new blog post '{title}' has been submitted! 🌟\n\n"
                    f"You can view it here: {response_data['url']}\n\n"
                    "Use /list to show a list of posts, /update to edit a post, or /post to create a new one."
                )
        else:
            await query.edit_message_text("❌ Failed to submit the blog post. Please try again later.")
    except Exception as e:
        await query.edit_message_text("❌ An error occurred while submitting the blog post. Please try again later.")
        logger.error(str(e))

async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    
    if user_id not in users_data:
        await update.message.reply_text("✏️ Please enter your Mataroa.blog API key first using the /start command.")
        return
    
    await update.message.reply_text(
        "✏️ Please enter the slug of the blog post you want to delete, or use /list to find the slug first.\n\n"
        "Be careful! This action cannot be undone."
    )
    return ENTER_DELETE_SLUG

async def enter_delete_slug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    slug = update.message.text
    api_key = users_data[update.message.from_user.id].api_key
    delete_url = f"{API_URL}{slug}/"
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.delete(delete_url, headers=headers)
        response_data = response.json()
        
        if response.status_code == 200 and response_data.get("ok"):
            await update.message.reply_text(
                f"✅ The blog post with slug '{slug}' has been deleted successfully. 🗑️\n\n"
                "You can create a new post with /post, or view your remaining posts with /list."
            )
        else:
            await update.message.reply_text("❌ Failed to delete the blog post. Please check the slug and try again.")
        
    except Exception as e:
        await update.message.reply_text("❌ An error occurred while deleting the blog post. Please try again later.")

    return ConversationHandler.END

async def update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("Update command received.")
    user_id = update.message.from_user.id

    if user_id not in users_data:
        logger.info(f"User {user_id} not found in users_data.")
        await update.message.reply_text("✏️ Please enter your Mataroa.blog API key first using the /start command.")
        return
    
    await update.message.reply_text("✏️ Please enter the slug of the blog post you want to update, or use /list to first find the slug.")
    logger.info(f"Prompted user {user_id} to enter the slug.")
    return ENTER_UPDATE_SLUG

async def enter_update_slug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    slug = update.message.text
    context.user_data['slug'] = slug

    api_key = users_data[update.message.from_user.id].api_key
    get_url = f"{API_URL}{slug}/"
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.get(get_url, headers=headers)
        response_data = response.json()

        if response.status_code == 200 and response_data.get("ok"):
            existing_post = response_data
            title = existing_post.get('title', '')
            body = existing_post.get('body', '')
            message = (
                f"✏️ You are updating the blog post with slug '{slug}'.\n\n"
                f"*Current Title:*\n{title}\n\n"
                f"*Current Body:*\n{body}\n\n"
                "✏️ Please enter the updated title for the blog post:"
            )
            await update.message.reply_markdown(message)
            return ENTER_UPDATED_TITLE
        else:
            await update.message.reply_text("❌ Failed to fetch the existing blog post. Please check the slug and try again.")
            return ConversationHandler.END

    except Exception as e:
        logger.error(f"An error occurred while fetching the existing blog post: {str(e)}")
        await update.message.reply_text("❌ An error occurred while fetching the existing blog post. Please try again later.")
        return ConversationHandler.END

async def enter_updated_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    updated_title = update.message.text.strip()
    if not updated_title:
        await update.message.reply_text("✏️ Please enter a valid title.")
        return ENTER_UPDATED_TITLE

    context.user_data['title'] = updated_title
    await update.message.reply_text("✏️ Please enter the updated body/content for the blog post:")
    return ENTER_UPDATED_BODY

async def enter_updated_body(update: Update, context: ContextTypes.DEFAULT_TYPE):
    updated_body = update.message.text.strip()
    if not updated_body:
        await update.message.reply_text("✏️ Please enter a valid body/content.")
        return ENTER_UPDATED_BODY

    context.user_data['body'] = updated_body
    
    keyboard = [
        [InlineKeyboardButton("Save as Draft", callback_data='draft')],
        [InlineKeyboardButton("Publish Now", callback_data='publish')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Your updates are ready! How do you want to proceed?\n\n"
        "👉 Save as Draft: Keep it private for now so you can review later.\n"
        "👉 Publish Now: Make it public immediately.",
        reply_markup=reply_markup
    )
    return ENTER_PUBLISH_CHOICE_UPDATE

async def list_posts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    
    if user_id not in users_data:
        await update.message.reply_text("✏️ Please enter your Mataroa.blog API key first using the /start command.")
        return

    context.user_data.clear()

    api_key = users_data[user_id].api_key
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.get(API_URL, headers=headers)
        response_data = response.json()
        
        if response.status_code == 200 and response_data.get("ok"):
            post_list = response_data.get("post_list", [])
            if not post_list:
                await update.message.reply_text("📭 You have no blog posts on Mataroa.blog.")
            else:
                message = "📄 Your blog posts on Mataroa.blog:\n"
                for post in post_list:
                    title = post.get("title", "No Title")
                    slug = post.get("slug", "No Slug")
                    url = post.get("url", "#")
                    message += f"🔗 [{title}]({url})\n- `{slug}`\n"
                
                await update.message.reply_markdown(
                    "Here's a list of your current blog posts on Mataroa.blog:\n\n" + message + 
                    "\n✏️ Use /update <slug> to modify a post, or /delete <slug> to remove one."
                )
        else:
            logger.error(f"Failed to list blog posts. Response: {response_data}")
            await update.message.reply_text("❌ Failed to list blog posts. Please try again later.")
        
    except Exception as e:
        logger.error(f"An error occurred while listing blog posts: {str(e)}")
        await update.message.reply_text("❌ An error occurred while listing blog posts. Please try again later.")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Operation cancelled. No worries! You can start over with /post or see your posts with /list.")
    return ConversationHandler.END

async def fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Sorry, I didn't understand that. Please try again.")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")

def main():
    application = Application.builder().token(TOKEN).build()

    conv_handler_start = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={ENTER_API_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_api_key)]},
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    conv_handler_post = ConversationHandler(
        entry_points=[CommandHandler('post', post)],
        states={
            ENTER_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_title)],
            ENTER_BODY: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_body)],
            ENTER_PUBLISH_CHOICE: [CallbackQueryHandler(button_handler)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        per_message=False,
    )

    conv_handler_delete = ConversationHandler(
        entry_points=[CommandHandler('delete', delete)],
        states={ENTER_DELETE_SLUG: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_delete_slug)]},
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    conv_handler_update = ConversationHandler(
        entry_points=[CommandHandler('update', update)],
        states={
            ENTER_UPDATE_SLUG: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_update_slug)],
            ENTER_UPDATED_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_updated_title)],
            ENTER_UPDATED_BODY: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_updated_body)],
            ENTER_PUBLISH_CHOICE_UPDATE: [CallbackQueryHandler(button_handler)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        per_message=False,
    )

    application.add_handler(conv_handler_start)
    application.add_handler(conv_handler_post)
    application.add_handler(conv_handler_delete)
    application.add_handler(conv_handler_update)
    application.add_handler(CommandHandler('list', list_posts))
    application.add_handler(CommandHandler('cancel', cancel))
    application.add_error_handler(error_handler)

    application.run_polling()

if __name__ == '__main__':
    main()
