"""LiveKitConnector that keeps only what the agent said.

A LiveKit agent publishes two transcripts on the ``lk.transcription`` topic:
its own speech and the caller's speech as it heard it. Both arrive from the
agent participant, so DeepEval's connector takes the caller's words for the
agent's reply and every turn after that is shifted by one. The caller's
transcript is tagged with the caller's own audio track, so it is dropped here.
"""

from __future__ import annotations

from deepeval.voice import LiveKitConnector

TRANSCRIBED_TRACK = "lk.transcribed_track_id"


class AgentConnector(LiveKitConnector):
    """LiveKitConnector that ignores the agent's transcripts of our own tracks."""

    def _on_transcript_stream(self, reader, participant_identity: str) -> None:
        attributes = getattr(getattr(reader, "info", None), "attributes", None) or {}
        if attributes.get(TRANSCRIBED_TRACK) in self._own_tracks():
            return  # the agent's transcript of the caller
        super()._on_transcript_stream(reader, participant_identity)

    def _own_tracks(self) -> set[str]:
        local = getattr(self._room, "local_participant", None)
        return set(getattr(local, "track_publications", None) or ())
