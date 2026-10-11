"""The command center. A separate process that only reads: it opens the database
read-only and asks the broker only read-only questions, so drawing a page can never
slow down or interfere with trading."""
