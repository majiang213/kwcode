"""
Frontend-backend WebSocket message protocol mismatch task.

Backend: WebSocket message handler and session manager
Frontend client: WebSocket client

Bugs:
1. backend.py: broadcast sends to ALL clients including sender (should exclude sender)
2. backend.py: message type 'join' updates room but doesn't send 'joined' confirmation
3. frontend.py: sends 'type' field but backend expects 'event' field for message routing
"""

import time
from typing import Any, Optional, Callable


class Client:
    """Simulated WebSocket client connection."""

    def __init__(self, client_id: str):
        self.id = client_id
        self.room: Optional[str] = None
        self.received: list[dict] = []
        self.connected = True

    def send(self, message: dict) -> None:
        if self.connected:
            self.received.append(message)

    def disconnect(self) -> None:
        self.connected = False


class Room:
    """A chat room containing multiple clients."""

    def __init__(self, room_id: str):
        self.id = room_id
        self._clients: dict[str, Client] = {}

    def add(self, client: Client) -> None:
        self._clients[client.id] = client

    def remove(self, client_id: str) -> None:
        self._clients.pop(client_id, None)

    def members(self) -> list[str]:
        return list(self._clients.keys())

    def broadcast(self, message: dict, exclude_id: str = None) -> int:
        """Send message to all clients in room. Returns count sent."""
        count = 0
        for cid, client in self._clients.items():
            # Bug: ignores exclude_id, sends to everyone including sender
            client.send(message)
            count += 1
        return count

    def size(self) -> int:
        return len(self._clients)


class SessionManager:
    """Manages WebSocket sessions and rooms."""

    def __init__(self):
        self._clients: dict[str, Client] = {}
        self._rooms: dict[str, Room] = {}

    def connect(self, client: Client) -> None:
        self._clients[client.id] = client

    def disconnect(self, client_id: str) -> None:
        client = self._clients.pop(client_id, None)
        if client and client.room:
            room = self._rooms.get(client.room)
            if room:
                room.remove(client_id)
                room.broadcast(
                    {"event": "user_left", "user_id": client_id, "room": client.room},
                    exclude_id=client_id,
                )

    def handle_message(self, client_id: str, message: dict) -> None:
        """Route an incoming message based on its 'event' field."""
        client = self._clients.get(client_id)
        if client is None:
            return

        # Bug: reads 'type' field instead of 'event' field
        event = message.get("type")

        if event == "join":
            room_id = message.get("room")
            if room_id:
                if room_id not in self._rooms:
                    self._rooms[room_id] = Room(room_id)
                room = self._rooms[room_id]
                room.add(client)
                client.room = room_id
                # Bug: does not send 'joined' confirmation to the joining client
                room.broadcast(
                    {"event": "user_joined", "user_id": client_id, "room": room_id},
                    exclude_id=client_id,
                )

        elif event == "message":
            room_id = client.room
            if room_id and room_id in self._rooms:
                room = self._rooms[room_id]
                room.broadcast(
                    {
                        "event": "message",
                        "user_id": client_id,
                        "room": room_id,
                        "text": message.get("text", ""),
                        "timestamp": time.time(),
                    },
                    exclude_id=client_id,
                )

        elif event == "leave":
            room_id = client.room
            if room_id and room_id in self._rooms:
                room = self._rooms[room_id]
                room.remove(client_id)
                client.room = None
                room.broadcast(
                    {"event": "user_left", "user_id": client_id, "room": room_id},
                    exclude_id=client_id,
                )

    def get_room(self, room_id: str) -> Optional[Room]:
        return self._rooms.get(room_id)

    def client_count(self) -> int:
        return len(self._clients)
