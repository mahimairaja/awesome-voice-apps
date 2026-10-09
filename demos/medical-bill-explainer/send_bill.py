"""Send a bill image into a running call, as the member's screen would.

    uv run python send_bill.py <room-name> samples/urgent-care-eob.png

Joins the room as a second participant, sends the file on the agent's
"bill-upload" byte stream topic, then leaves.
"""

import asyncio
import os
import sys

from dotenv import load_dotenv
from livekit import api, rtc

load_dotenv()


async def main(room_name: str, path: str) -> None:
    token = (
        api.AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
        .with_identity("bill-sender")
        .with_grants(api.VideoGrants(room_join=True, room=room_name))
        .to_jwt()
    )
    room = rtc.Room()
    await room.connect(os.environ["LIVEKIT_URL"], token)
    try:
        info = await room.local_participant.send_file(path, topic="bill-upload", compress=False)
        print(f"Sent {info.name} ({info.size} bytes)")
        await asyncio.sleep(1)
    finally:
        await room.disconnect()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1], sys.argv[2]))
