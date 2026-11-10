from typing import List, Dict, Any, Optional
from mcp.server.fastmcp import FastMCP
from whatsapp import (
    search_contacts as whatsapp_search_contacts,
    list_chats as whatsapp_list_chats,
    get_message_context as whatsapp_get_message_context,
    download_media as whatsapp_download_media
)

# Initialize FastMCP server
mcp = FastMCP("whatsapp")

# Read-only by design: no tool here sends, reacts or changes anything, so a Claude run reading other people's messages
# has nothing to act with.


@mcp.tool()
def search_contacts(query: str) -> List[Dict[str, Any]]:
    """Search WhatsApp contacts by name or phone number.
    
    Args:
        query: Search term to match against contact names or phone numbers
    """
    contacts = whatsapp_search_contacts(query)
    return contacts


@mcp.tool()
def list_chats(
    query: Optional[str] = None,
    limit: int = 20,
    page: int = 0,
    include_last_message: bool = True,
    sort_by: str = "last_active"
) -> List[Dict[str, Any]]:
    """Get WhatsApp chats matching specified criteria.
    
    Args:
        query: Optional search term to filter chats by name or JID
        limit: Maximum number of chats to return (default 20)
        page: Page number for pagination (default 0)
        include_last_message: Whether to include the last message in each chat (default True)
        sort_by: Field to sort results by, either "last_active" or "name" (default "last_active")
    """
    chats = whatsapp_list_chats(
        query=query,
        limit=limit,
        page=page,
        include_last_message=include_last_message,
        sort_by=sort_by
    )
    return chats


@mcp.tool()
def get_message_context(
    message_id: str,
    before: int = 5,
    after: int = 5
) -> Dict[str, Any]:
    """Get context around a specific WhatsApp message.
    
    Args:
        message_id: The ID of the message to get context for
        before: Number of messages to include before the target message (default 5)
        after: Number of messages to include after the target message (default 5)
    """
    context = whatsapp_get_message_context(message_id, before, after)
    return context


@mcp.tool()
def download_media(message_id: str, chat_jid: str) -> Dict[str, Any]:
    """Download media from a WhatsApp message and get the local file path.
    
    Args:
        message_id: The ID of the message containing the media
        chat_jid: The JID of the chat containing the message
    
    Returns:
        A dictionary containing success status, a status message, and the file path if successful
    """
    file_path = whatsapp_download_media(message_id, chat_jid)
    
    if file_path:
        return {
            "success": True,
            "message": "Media downloaded successfully",
            "file_path": file_path
        }
    else:
        return {
            "success": False,
            "message": "Failed to download media"
        }

# ---------- enhanced bridge tools ----------
import extras  # noqa: E402


@mcp.tool()
def search_all(query: str, limit: int = 30) -> List[Dict[str, Any]]:
    """Search text in all messages AND in transcripts of voice notes, newest first. Use "a|b" for any of several words — Hebrew has many forms of a word (e.g. "ביצה|ביצים|ביצי")."""
    return extras.search(query, limit)


@mcp.tool()
def chat_history(chat: str, after: Optional[str] = None, before: Optional[str] = None, limit: int = 200) -> Dict[str, Any]:
    """One conversation in order, oldest first, with every voice note already transcribed into text — the tool for
    "what did X ask me today", "how many eggs did my wife want", "summarize the group since yesterday".

    Args:
        chat: a contact or group name (or part of it, e.g. "אמא"), a phone number, or a chat JID
        after: ISO date/time, only messages after it (default: the last 24 hours)
        before: ISO date/time, only messages before it
        limit: most recent N messages in the window (default 200)
    """
    return extras.chat_history(chat, after, before, limit)


@mcp.tool()
def transcribe_voice_note(chat: str, message_id: Optional[str] = None, wait_seconds: int = 120) -> Dict[str, Any]:
    """Transcribe voice notes that have no text yet (e.g. ones that arrived before the agent was running) and return the
    text. Use it when chat_history shows "(voice note, not transcribed yet)".

    Args:
        chat: contact or group name, phone number, or chat JID
        message_id: one voice note's id (from chat_history); omit for the chat's recent untranscribed notes (last 7 days, up to 10)
        wait_seconds: how long to wait for the text (default 120)
    """
    return extras.transcribe_voice_note(chat, message_id, wait_seconds)


if __name__ == "__main__":
    # Initialize and run the server
    mcp.run(transport='stdio')