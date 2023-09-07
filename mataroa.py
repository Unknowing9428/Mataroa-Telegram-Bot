import json
import os
import requests
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Updater, CommandHandler, MessageHandler, ConversationHandler, CallbackContext, Filters
import logging
import sys
from datetime import datetime

# Configure the logging settings
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO  # Set the desired logging level (INFO, DEBUG, ERROR, etc.)
)

logger = logging.getLogger(__name__)  
sys.stdout = sys.stderr

# conversation states
ENTER_API_KEY = 0
ENTER_TITLE = 1
ENTER_BODY = 2
ENTER_PUBLISH_CHOICE = 3
ENTER_DELETE_SLUG = 4
ENTER_UPDATE_SLUG = 5
ENTER_UPDATED_TITLE = 6
ENTER_UPDATED_BODY = 7
ENTER_PUBLISH_CHOICE_UPDATE = 8

# user data dictionary
users_data = {}

# API URL
API_URL = "https://mataroa.blog/api/posts/"

# bot token 
TOKEN = "6513735892:AAG_fnTs8jpxDYt3aEJnWQ4NxfDalKrww9o"

# users.json path
USERS_JSON_PATH = "users.json"

# user data dictionary from the existing JSON file
if os.path.exists(USERS_JSON_PATH):
    with open(USERS_JSON_PATH, "r") as user_file:
        users_data = json.load(user_file)
else:
    users_data = {}

def start(update: Update, context: CallbackContext):
    update.message.reply_text("👋 Welcome to the Mataroa.blog bot! To get started, please enter your Mataroa.blog API key.")
    return ENTER_API_KEY

def enter_api_key(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    api_key = update.message.text.strip()

    logger.info(f"Received API key '{api_key}' from user {user_id}")

    if user_id in users_data:
        users_data[user_id]["api_key"] = api_key
    else:
        users_data[user_id] = {"api_key": api_key}

    try:
        with open(USERS_JSON_PATH, "w") as user_file:
            json.dump(users_data, user_file)

        update.message.reply_text("✅ Your Mataroa.blog API key has been updated.")
    except Exception as e:
        update.message.reply_text("❌ An error occurred while updating your API key. Please try again later.")
        logger.error(str(e))

    return ConversationHandler.END

def post(update, context):
    try:
        user_id = update.message.from_user.id
        if user_id not in users_data:
            update.message.reply_text("🔑 Please enter your Mataroa.blog API key first using the /start command.")
            return
        
        api_key = users_data[user_id]["api_key"]
        logger.info(f"User {user_id} with API key '{api_key}' is creating a new blog post.")
        update.message.reply_text("📝 Please enter the title of your new blog post:")
        return ENTER_TITLE
    except Exception as e:
        update.message.reply_text("❌ An error occurred. Please try again later.")
        logger.error(str(e))
        return ConversationHandler.END

def enter_title(update, context):
    try:
        title = update.message.text.strip()  # Remove leading/trailing whitespace
        logger.info(f"Received title '{title}' from user {update.message.from_user.id}")

        if not title:
            update.message.reply_text("Please enter a valid title for your blog post.")
            return ENTER_TITLE

        context.user_data['title'] = title
        update.message.reply_text("✏️ Please enter the body/content of your new blog post:")
        return ENTER_BODY
    except Exception as e:
        update.message.reply_text("❌ An error occurred. Please try again later.")
        logger.error(str(e))
        return ConversationHandler.END

def enter_body(update, context):
    try:
        body = update.message.text.strip()  # Remove leading/trailing whitespace
        logger.info(f"Received body/content '{body}' from user {update.message.from_user.id}")
        if not body:
            update.message.reply_text("✏️ Please enter a valid body/content for your blog post.")
            return ENTER_BODY
        
        context.user_data['body'] = body
        # Ask the user whether they want to save as draft or publish now
        keyboard = [['Save as Draft', 'Publish Now']]
        markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
        update.message.reply_text("Do you want to save this post as a draft or publish it now?", reply_markup=markup)
        return ENTER_PUBLISH_CHOICE
    except Exception as e:
        update.message.reply_text("❌ An error occurred. Please try again later.")
        logger.error(str(e))
        return ConversationHandler.END

def enter_publish_choice(update, context):
    try:
        user_choice = update.message.text
        if user_choice == 'Save as Draft':
            # Handle saving as a draft (without 'published_at')
            context.user_data['published_at'] = None
        elif user_choice == 'Publish Now':
            # Handle publishing now (set 'published_at' to the current date/time)
            context.user_data['published_at'] = datetime.now().strftime("%Y-%m-%d")

        # Retrieve other data from user_data dictionary
        api_key = users_data[update.message.from_user.id]["api_key"]
        title = context.user_data['title']
        body = context.user_data['body']

        post_data = {
            "title": title,
            "body": body,
            "published_at": context.user_data['published_at']
        }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        try:
            response = requests.post(API_URL, json=post_data, headers=headers)
            response_data = response.json()

            if response.status_code == 200 and response_data.get("ok"):
                update.message.reply_text(f"✅ Your new blog post '{title}' has been submitted! You can view it at {response_data['url']}.")
            else:
                update.message.reply_text("❌ Failed to submit the blog post. Please try again later.")

        except Exception as e:
            update.message.reply_text("❌ An error occurred while submitting the blog post. Please try again later.")
            logger.error(str(e))

        return ConversationHandler.END

    except Exception as e:
        update.message.reply_text("❌ An error occurred. Please try again later.")
        logger.error(str(e))
        return ConversationHandler.END

def enter_publish_choice_update(update, context):
    try:
        user_choice = update.message.text
        if user_choice == 'Save as Draft':
            # Handle saving as a draft (without 'published_at')
            context.user_data['published_at'] = None
        elif user_choice == 'Publish Now':
            # Handle publishing now (set 'published_at' to the current date/time)
            context.user_data['published_at'] = datetime.now().strftime("%Y-%m-%d")

        # Retrieve other data from user_data dictionary
        api_key = users_data[update.message.from_user.id]["api_key"]
        updated_title = context.user_data['updated_title']
        updated_body = context.user_data['updated_body']

        update_data = {
            "title": updated_title,
            "body": updated_body,
            "published_at": context.user_data['published_at']
        }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        try:
            response = requests.post(API_URL, json=update_data, headers=headers)
            response_data = response.json()

            if response.status_code == 200 and response_data.get("ok"):
                update.message.reply_text(f"✅ Your updated blog post '{updated_title}' has been submitted! You can view it at {response_data['url']}.")
            else:
                update.message.reply_text(f"❌ Failed to {user_choice.lower()} the blog post. Please try again later.")

        except Exception as e:
            update.message.reply_text(f"❌ An error occurred while {user_choice.lower()} the blog post. Please try again later.")
            logger.error(str(e))

        return ConversationHandler.END

    except Exception as e:
        update.message.reply_text("❌ An error occurred. Please try again later.")
        logger.error(str(e))
        return ConversationHandler.END

def delete(update, context):
    user_id = update.message.from_user.id
    
    if user_id not in users_data:
        update.message.reply_text("✏️ Please enter your Mataroa.blog API key first using the /start command.")
        return
    
    api_key = users_data[user_id]["api_key"]
    update.message.reply_text("✏️ Please enter the slug of the blog post you want to delete, or use /list to first find the slug.")
    return ENTER_DELETE_SLUG

def enter_delete_slug(update, context):
    slug = update.message.text
    api_key = users_data[update.message.from_user.id]["api_key"]
    delete_url = f"{API_URL}{slug}/"
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.delete(delete_url, headers=headers)
        response_data = response.json()
        
        if response.status_code == 200 and response_data.get("ok"):
            update.message.reply_text(f"✅ The blog post with slug '{slug}' has been deleted.")
        else:
            update.message.reply_text("❌ Failed to delete the blog post. Please check the slug and try again.")
        
    except Exception as e:
        update.message.reply_text("❌ An error occurred while deleting the blog post. Please try again later.")

    return ConversationHandler.END

def update(update, context):
    user_id = update.message.from_user.id

    if user_id not in users_data:
        update.message.reply_text("✏️ Please enter your Mataroa.blog API key first using the /start command.")
        return
    
    api_key = users_data[user_id]["api_key"]
    update.message.reply_text("✏️ Please enter the slug of the blog post you want to update, or use /list to first find the slug.")
    return ENTER_UPDATE_SLUG

def enter_update_slug(update, context):
    slug = update.message.text
    context.user_data['slug'] = slug

    # Fetch the existing blog post content using the slug
    api_key = users_data[update.message.from_user.id]["api_key"]
    get_url = f"{API_URL}{slug}/"
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.get(get_url, headers=headers)
        response_data = response.json()

        if response.status_code == 200 and response_data.get("ok"):
            # Include the fetched blog post content in the message to make it easier for editing
            existing_post = response_data  # Corrected this line
            update.message.reply_text(f"✏️ You are updating the blog post with slug '{slug}'.")
            update.message.reply_text(f"✏️ Title:\n{existing_post.get('title')}")
            update.message.reply_text(f"✏️ Body:\n{existing_post.get('body')}")
            update.message.reply_text("✏️ Please enter the updated title for the blog post:")
            return ENTER_UPDATED_TITLE
        else:
            update.message.reply_text("❌ Failed to fetch the existing blog post. Please check the slug and try again.")
            return ConversationHandler.END

    except Exception as e:
        update.message.reply_text("❌ An error occurred while fetching the existing blog post. Please try again later.")
        return ConversationHandler.END

def enter_updated_title(update, context):
    updated_title = update.message.text
    context.user_data['updated_title'] = updated_title
    update.message.reply_text("✏️ Please enter the updated body/content for the blog post:")
    return ENTER_UPDATED_BODY

def enter_updated_body(update, context):
    updated_body = update.message.text
    
    # Store updated_body in context.user_data
    context.user_data['updated_body'] = updated_body
    
    # Ask the user whether they want to save as a draft or publish now
    keyboard = [['Save as Draft', 'Publish Now']]
    markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
    update.message.reply_text("Do you want to save this updated post as a draft or publish it now?", reply_markup=markup)
    return ENTER_PUBLISH_CHOICE_UPDATE

def list_posts(update, context):
    user_id = update.message.from_user.id
    
    if user_id not in users_data:
        update.message.reply_text("✏️ Please enter your Mataroa.blog API key first using the /start command.")
        return

    context.user_data.clear()
    # update.message.reply_text("✅ Ongoing conversation canceled. Here is the list of your blog posts:")

    api_key = users_data[user_id]["api_key"]
    
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
                update.message.reply_text("You have no blog posts on Mataroa.blog.")
            else:
                message = "Your blog posts on Mataroa.blog:\n"
                for post in post_list:
                    title = post.get("title", "No Title")
                    slug = post.get("slug", "No Slug")
                    url = post.get("url", "#")
                    message += f"- [{title}]({url}) ({slug})\n"
                update.message.reply_markdown(message)
        else:
            update.message.reply_text("❌ Failed to list blog posts. Please try again later.")
        
    except Exception as e:
        update.message.reply_text("❌ An error occurred while listing blog posts. Please try again later.")

def cancel(update, context):
    user_id = update.message.from_user.id
    update.message.reply_text("Cancelled.")
    return ConversationHandler.END

def fallback(update, context):
    update.message.reply_text("❌ Sorry, I didn't understand that. Please try again.")

def main():
    updater = Updater(token=TOKEN, use_context=True)
    dispatcher = updater.dispatcher

    conv_handler_start = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={ENTER_API_KEY: [MessageHandler(Filters.text & ~Filters.command, enter_api_key)]},
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    conv_handler_post = ConversationHandler(
        entry_points=[CommandHandler('post', post)],
        states={
            ENTER_TITLE: [MessageHandler(Filters.text & ~Filters.command, enter_title)],
            ENTER_BODY: [MessageHandler(Filters.text & ~Filters.command, enter_body)],
            ENTER_PUBLISH_CHOICE: [MessageHandler(Filters.text & ~Filters.command, enter_publish_choice)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    conv_handler_delete = ConversationHandler(
        entry_points=[CommandHandler('delete', delete)],
        states={ENTER_DELETE_SLUG: [MessageHandler(Filters.text & ~Filters.command, enter_delete_slug)]},
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    conv_handler_update = ConversationHandler(
        entry_points=[CommandHandler('update', update)],
        states={
            ENTER_UPDATE_SLUG: [MessageHandler(Filters.text & ~Filters.command, enter_update_slug)],
            ENTER_UPDATED_TITLE: [MessageHandler(Filters.text & ~Filters.command, enter_updated_title)],
            ENTER_UPDATED_BODY: [MessageHandler(Filters.text & ~Filters.command, enter_updated_body)],
            ENTER_PUBLISH_CHOICE_UPDATE: [MessageHandler(Filters.text & ~Filters.command, enter_publish_choice_update)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    dispatcher.add_handler(conv_handler_start)
    dispatcher.add_handler(conv_handler_post)
    dispatcher.add_handler(conv_handler_delete)
    dispatcher.add_handler(conv_handler_update)
    dispatcher.add_handler(CommandHandler('list', list_posts))
    dispatcher.add_handler(CommandHandler('cancel', cancel))

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()