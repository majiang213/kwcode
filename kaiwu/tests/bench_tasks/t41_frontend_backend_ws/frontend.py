"""Frontend WebSocket client."""

from typing import Optional, Callable
from backend import Client, SessionManager


class WSClient:
    """Frontend WebSocket client that communicates with the backend session manager."""

    def __init__(self, client_id: str, session: SessionManager):
        self._id = client_id
        self._session = session
        self._client = Client(client_id)
        self._session.connect(self._client)
        self._current_room: Optional[str] = None
        self._on_message: Optional[Callable] = None

    def on_message(self, handler: Callable) -> None:
        self._on_message = handler

    def _send(self, message: dict) -> None:
        """Send a message to the backend."""
        self._session.handle_message(self._id, message)

    def join(self, room: str) -> None:
        """Join a chat room."""
        # Bug: sends 'type' field but backend expects 'event' field
        self._send({"type": "join", "room": room})
        self._current_room = room

    def send_message(self, text: str) -> None:
        """Send a chat message to the current room."""
        # Bug: sends 'type' field but backend expects 'event' field
        self._send({"type": "message", "text": text})

    def leave(self) -> None:
        """Leave the current room."""
        # Bug: sends 'type' field but backend expects 'event' field
        self._send({"type": "leave"})
        self._current_room = None

    def received_messages(self) -> list[dict]:
        return list(self._client.received)

    def disconnect(self) -> None:
        self._session.disconnect(self._id)
        self._client.disconnect()
