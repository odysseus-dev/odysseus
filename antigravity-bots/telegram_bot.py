import os
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from google.antigravity import Agent, LocalAgentConfig, CapabilitiesConfig

# Define the async function to handle incoming Telegram text messages
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_message = update.message.text
    chat_id = update.message.chat_id

    # Send an initial "thinking..." message
    status_message = await context.bot.send_message(chat_id=chat_id, text="Agent is thinking...")

    # Configure the Antigravity agent (Enable write capabilities if you want it to modify files)
    config = LocalAgentConfig(
        system_instructions="You are a remote assistant managed via Telegram. Keep responses concise.",
        capabilities=CapabilitiesConfig()
    )

    try:
        # Initialize the Antigravity Agent
        async with Agent(config) as agent:
            # Send the user's message to the agent
            response = await agent.chat(user_message)
            
            # Buffer the response tokens 
            # Note: You can also stream this by editing the Telegram message in chunks
            full_reply = ""
            async for token in response:
                full_reply += token
            
            # Update the Telegram message with the final response
            await context.bot.edit_message_text(
                chat_id=chat_id, 
                message_id=status_message.message_id, 
                text=full_reply or "Agent completed the task."
            )

    except Exception as e:
        await context.bot.edit_message_text(
            chat_id=chat_id, 
            message_id=status_message.message_id, 
            text=f"Agent encountered an error: {e}"
        )

# Start command handler
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text('Hello! I am your Antigravity Remote Agent. Send me a task.')

if __name__ == '__main__':
    # Add your Telegram Bot Token from BotFather
    TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
    
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Starting Antigravity Telegram Bot...")
    app.run_polling()
