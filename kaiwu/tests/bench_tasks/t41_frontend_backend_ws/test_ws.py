"""Tests for frontend-backend WebSocket protocol consistency."""

import pytest
from backend import Client, Room, SessionManager
from frontend import WSClient


class TestRoom:
    def test_broadcast_excludes_sender(self):
        """Broadcast should not send to the excluded client."""
        room = Room("r1")
        c1 = Client("c1")
        c2 = Client("c2")
        c3 = Client("c3")
        room.add(c1)
        room.add(c2)
        room.add(c3)
        room.broadcast({"event": "test"}, exclude_id="c1")
        assert len(c1.received) == 0
        assert len(c2.received) == 1
        assert len(c3.received) == 1

    def test_broadcast_to_all_when_no_exclude(self):
        room = Room("r1")
        c1 = Client("c1")
        c2 = Client("c2")
        room.add(c1)
        room.add(c2)
        count = room.broadcast({"event": "test"})
        assert count == 2
        assert len(c1.received) == 1
        assert len(c2.received) == 1

    def test_members_list(self):
        room = Room("r1")
        room.add(Client("c1"))
        room.add(Client("c2"))
        assert set(room.members()) == {"c1", "c2"}

    def test_remove_client(self):
        room = Room("r1")
        room.add(Client("c1"))
        room.remove("c1")
        assert room.size() == 0


class TestSessionManager:
    def test_join_room_adds_client(self):
        sm = SessionManager()
        c = Client("c1")
        sm.connect(c)
        sm.handle_message("c1", {"event": "join", "room": "room-1"})
        room = sm.get_room("room-1")
        assert room is not None
        assert "c1" in room.members()

    def test_join_sends_confirmation_to_joiner(self):
        """Joining client should receive a 'joined' confirmation."""
        sm = SessionManager()
        c = Client("c1")
        sm.connect(c)
        sm.handle_message("c1", {"event": "join", "room": "room-1"})
        events = [m["event"] for m in c.received]
        assert "joined" in events

    def test_join_notifies_existing_members(self):
        sm = SessionManager()
        c1 = Client("c1")
        c2 = Client("c2")
        sm.connect(c1)
        sm.connect(c2)
        sm.handle_message("c1", {"event": "join", "room": "room-1"})
        sm.handle_message("c2", {"event": "join", "room": "room-1"})
        # c1 should receive 'user_joined' for c2
        events = [m["event"] for m in c1.received]
        assert "user_joined" in events

    def test_message_broadcast_excludes_sender(self):
        sm = SessionManager()
        c1 = Client("c1")
        c2 = Client("c2")
        sm.connect(c1)
        sm.connect(c2)
        sm.handle_message("c1", {"event": "join", "room": "room-1"})
        sm.handle_message("c2", {"event": "join", "room": "room-1"})
        c1.received.clear()
        c2.received.clear()
        sm.handle_message("c1", {"event": "message", "text": "hello"})
        # c1 should NOT receive its own message
        assert not any(m.get("text") == "hello" for m in c1.received)
        # c2 should receive it
        assert any(m.get("text") == "hello" for m in c2.received)

    def test_leave_removes_from_room(self):
        sm = SessionManager()
        c = Client("c1")
        sm.connect(c)
        sm.handle_message("c1", {"event": "join", "room": "room-1"})
        sm.handle_message("c1", {"event": "leave"})
        room = sm.get_room("room-1")
        assert "c1" not in room.members()

    def test_disconnect_removes_from_room(self):
        sm = SessionManager()
        c1 = Client("c1")
        c2 = Client("c2")
        sm.connect(c1)
        sm.connect(c2)
        sm.handle_message("c1", {"event": "join", "room": "room-1"})
        sm.handle_message("c2", {"event": "join", "room": "room-1"})
        sm.disconnect("c1")
        room = sm.get_room("room-1")
        assert "c1" not in room.members()


class TestWSClient:
    def test_join_room(self):
        sm = SessionManager()
        client = WSClient("c1", sm)
        client.join("room-1")
        room = sm.get_room("room-1")
        assert room is not None
        assert "c1" in room.members()

    def test_join_receives_confirmation(self):
        sm = SessionManager()
        client = WSClient("c1", sm)
        client.join("room-1")
        events = [m["event"] for m in client.received_messages()]
        assert "joined" in events

    def test_send_message_received_by_others(self):
        sm = SessionManager()
        c1 = WSClient("c1", sm)
        c2 = WSClient("c2", sm)
        c1.join("room-1")
        c2.join("room-1")
        c1._client.received.clear()
        c2._client.received.clear()
        c1.send_message("hello")
        msgs = c2.received_messages()
        assert any(m.get("text") == "hello" for m in msgs)

    def test_sender_does_not_receive_own_message(self):
        sm = SessionManager()
        c1 = WSClient("c1", sm)
        c2 = WSClient("c2", sm)
        c1.join("room-1")
        c2.join("room-1")
        c1._client.received.clear()
        c1.send_message("hello")
        assert not any(m.get("text") == "hello" for m in c1.received_messages())

    def test_leave_room(self):
        sm = SessionManager()
        c1 = WSClient("c1", sm)
        c1.join("room-1")
        c1.leave()
        room = sm.get_room("room-1")
        assert "c1" not in room.members()
