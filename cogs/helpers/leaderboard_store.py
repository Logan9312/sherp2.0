import os
import sqlite3
from typing import List, Optional, Tuple

# Points for reacting with a board's emoji. A reaction costs its reactor
# REACTION_COST while the message is still waiting to reach the board. When
# the message is posted to the board, everyone still reacting becomes a
# "backer" and earns BACKER_REWARD (a net +1). Reactions added once the message
# is already on the board are free. While a message is on the board its author
# earns one point for every reaction on it, and loses it again if the reaction
# is removed.
REACTION_COST = 1
BACKER_REWARD = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scores (
    board TEXT NOT NULL,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    score INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (board, guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS messages (
    board TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    guild_id INTEGER NOT NULL,
    author_id INTEGER,
    boarded INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (board, message_id)
);

CREATE TABLE IF NOT EXISTS reactions (
    board TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    backer INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (board, message_id, user_id)
);
"""


class LeaderboardStore:
    # Persists leaderboard scores in SQLite. Every method runs synchronously in
    # a single transaction, so calls made from the event loop never interleave.
    #
    # ``board`` is a stable key per leaderboard (e.g. "onphone"). ``author_id``
    # is ``None`` for authors that must not earn points (bots).
    #
    # A reaction row is deleted when a non-backer removes their reaction, so an
    # inactive row always belongs to a backer who un-reacted after the message
    # reached the board (their cost and reward stay locked in).

    def __init__(self, path: str):
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._conn = sqlite3.connect(path)
        with self._conn:
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def add_reaction(
        self,
        board: str,
        guild_id: int,
        message_id: int,
        author_id: Optional[int],
        user_id: int,
    ) -> None:
        with self._conn:
            guild_id, author_id, boarded = self._ensure_message(
                board, guild_id, message_id, author_id
            )
            reaction = self._get_reaction(board, message_id, user_id)
            if reaction is not None and reaction[1]:
                return  # Duplicate event, the reaction is already counted.

            if reaction is None:
                self._conn.execute(
                    "INSERT INTO reactions (board, message_id, user_id) "
                    "VALUES (?, ?, ?)",
                    (board, message_id, user_id),
                )
                if not boarded:
                    self._add_points(board, guild_id, user_id, -REACTION_COST)
            else:
                # A backer reacting again; they already paid and were rewarded.
                self._set_active(board, message_id, user_id, True)

            if boarded and author_id is not None:
                self._add_points(board, guild_id, author_id, 1)

    def remove_reaction(self, board: str, message_id: int, user_id: int) -> None:
        with self._conn:
            self._remove_reaction(board, message_id, user_id)

    def clear_reactions(self, board: str, message_id: int) -> None:
        with self._conn:
            rows = self._conn.execute(
                "SELECT user_id FROM reactions "
                "WHERE board = ? AND message_id = ? AND active = 1",
                (board, message_id),
            ).fetchall()
            for (user_id,) in rows:
                self._remove_reaction(board, message_id, user_id)

    def mark_boarded(
        self,
        board: str,
        guild_id: int,
        message_id: int,
        author_id: Optional[int],
    ) -> bool:
        # Rewards a message's backers and author the first time it reaches the
        # board. Returns ``False`` if the message had already been rewarded.
        with self._conn:
            guild_id, author_id, boarded = self._ensure_message(
                board, guild_id, message_id, author_id
            )
            if boarded:
                return False

            self._conn.execute(
                "UPDATE messages SET boarded = 1 WHERE board = ? AND message_id = ?",
                (board, message_id),
            )
            backers = [
                user_id
                for (user_id,) in self._conn.execute(
                    "SELECT user_id FROM reactions "
                    "WHERE board = ? AND message_id = ? AND active = 1",
                    (board, message_id),
                ).fetchall()
            ]
            self._conn.execute(
                "UPDATE reactions SET backer = 1 "
                "WHERE board = ? AND message_id = ? AND active = 1",
                (board, message_id),
            )
            for user_id in backers:
                self._add_points(board, guild_id, user_id, BACKER_REWARD)
            if author_id is not None:
                self._add_points(board, guild_id, author_id, len(backers))
            return True

    def forget_pending_message(self, board: str, message_id: int) -> None:
        # Refunds everyone who reacted to a deleted message that never reached
        # the board. Rewards for boarded messages are final and left untouched.
        with self._conn:
            message = self._get_message(board, message_id)
            if message is None or message[2]:
                return

            guild_id = message[0]
            rows = self._conn.execute(
                "SELECT user_id FROM reactions WHERE board = ? AND message_id = ?",
                (board, message_id),
            ).fetchall()
            for (user_id,) in rows:
                self._add_points(board, guild_id, user_id, REACTION_COST)
            self._conn.execute(
                "DELETE FROM reactions WHERE board = ? AND message_id = ?",
                (board, message_id),
            )
            self._conn.execute(
                "DELETE FROM messages WHERE board = ? AND message_id = ?",
                (board, message_id),
            )

    def get_scores(self, board: str, guild_id: int) -> List[Tuple[int, int]]:
        # Returns ``(user_id, score)`` pairs, highest score first.
        return self._conn.execute(
            "SELECT user_id, score FROM scores WHERE board = ? AND guild_id = ? "
            "ORDER BY score DESC, user_id ASC",
            (board, guild_id),
        ).fetchall()

    def _remove_reaction(self, board: str, message_id: int, user_id: int) -> None:
        reaction = self._get_reaction(board, message_id, user_id)
        if reaction is None or not reaction[1]:
            return

        message = self._get_message(board, message_id)
        if message is None:
            return
        guild_id, author_id, boarded = message

        is_backer = reaction[0]
        if is_backer:
            # Backers keep their cost and reward once the message was boarded.
            self._set_active(board, message_id, user_id, False)
        else:
            self._conn.execute(
                "DELETE FROM reactions "
                "WHERE board = ? AND message_id = ? AND user_id = ?",
                (board, message_id, user_id),
            )
            if not boarded:
                self._add_points(board, guild_id, user_id, REACTION_COST)

        if boarded and author_id is not None:
            self._add_points(board, guild_id, author_id, -1)

    def _ensure_message(
        self,
        board: str,
        guild_id: int,
        message_id: int,
        author_id: Optional[int],
    ) -> Tuple[int, Optional[int], bool]:
        # Returns the stored ``(guild_id, author_id, boarded)`` of the message,
        # creating its row first if this is the first time it is seen.
        self._conn.execute(
            "INSERT OR IGNORE INTO messages (board, message_id, guild_id, author_id) "
            "VALUES (?, ?, ?, ?)",
            (board, message_id, guild_id, author_id),
        )
        message = self._get_message(board, message_id)
        if message is None:
            raise RuntimeError(f"{board} message {message_id} was not stored")
        return message

    def _get_message(
        self, board: str, message_id: int
    ) -> Optional[Tuple[int, Optional[int], bool]]:
        row = self._conn.execute(
            "SELECT guild_id, author_id, boarded FROM messages "
            "WHERE board = ? AND message_id = ?",
            (board, message_id),
        ).fetchone()
        if row is None:
            return None
        return row[0], row[1], bool(row[2])

    def _get_reaction(
        self, board: str, message_id: int, user_id: int
    ) -> Optional[Tuple[bool, bool]]:
        row = self._conn.execute(
            "SELECT backer, active FROM reactions "
            "WHERE board = ? AND message_id = ? AND user_id = ?",
            (board, message_id, user_id),
        ).fetchone()
        if row is None:
            return None
        return bool(row[0]), bool(row[1])

    def _set_active(
        self, board: str, message_id: int, user_id: int, active: bool
    ) -> None:
        self._conn.execute(
            "UPDATE reactions SET active = ? "
            "WHERE board = ? AND message_id = ? AND user_id = ?",
            (int(active), board, message_id, user_id),
        )

    def _add_points(self, board: str, guild_id: int, user_id: int, points: int) -> None:
        self._conn.execute(
            "INSERT INTO scores (board, guild_id, user_id, score) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (board, guild_id, user_id) "
            "DO UPDATE SET score = score + excluded.score",
            (board, guild_id, user_id, points),
        )
