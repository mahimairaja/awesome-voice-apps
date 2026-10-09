"""A simulated phone line between the caller and the agent.

Browser audio reaches the agent wideband and clean. A real outage call does not:
it crosses an 8 kHz telephone codec and, on a weak cell signal, a network that
drops and delays packets. PhoneLine recreates that path inside the worker, before
voice activity detection and speech-to-text see the audio, so the agent hears
what it would hear on the phone network.

Three lines:
- web: passthrough, the clean baseline.
- landline: resampled to 8 kHz and mu-law companded (G.711 style).
- cell: landline plus bursty packet loss (Gilbert-Elliott) and network jitter.

The receive-side fixes a telephony stack can switch on:
- an adaptive jitter buffer, so late packets are waited for instead of dropped,
- forward error correction (Opus in-band FEC style): a lost packet is rebuilt
  from the copy carried in the next one,
- packet loss concealment: a gap neither can fill repeats the last good audio,
  fading out, instead of going silent.

It is a simulation for a demo, not a codec implementation. The numbers are in
LINE_PROFILES and are deliberately plain.
"""

import math
import random
from collections import deque
from dataclasses import asdict, dataclass

import numpy as np
from livekit import rtc
from livekit.agents.voice.io import AudioInput

PHONE_RATE = 8000
PACKET = 160  # 20 ms at 8 kHz, the usual RTP packet size for telephone audio
PACKET_SECONDS = PACKET / PHONE_RATE
LINES = ("web", "landline", "cell")
LINE_LABELS = {"web": "Web audio", "landline": "Landline", "cell": "Bad cell"}

# Jitter buffer depth in seconds: a fixed shallow buffer without the fixes, an
# adaptive one sized for a congested cell network with them.
STATIC_BUFFER = 0.06
ADAPTIVE_BUFFER = 0.2

# Gilbert-Elliott burst loss: a good state with rare loss and a bad state (a
# fade, a handover) where most packets vanish. About 7% of packets on average,
# arriving in bursts the way cell loss does.
TO_BAD = 0.05
TO_GOOD = 0.4
LOSS_GOOD = 0.01
LOSS_BAD = 0.6

# Network delay variation: most packets wobble by tens of milliseconds, and a
# few are held up by a burst of congestion.
JITTER_SD = 0.035
SPIKE_CHANCE = 0.02
SPIKE_DELAY = 0.15

# Keep the last few seconds the caller said, as the agent heard it, for playback.
TAPE_SECONDS = 15
PREROLL_SECONDS = 0.4


def mu_law(samples: np.ndarray, mu: int = 255) -> np.ndarray:
    """Round-trip int16 audio through 8-bit mu-law companding."""
    x = samples.astype(np.float32) / 32768.0
    y = np.sign(x) * np.log1p(mu * np.abs(x)) / math.log1p(mu)
    y = np.round(y * 127.0) / 127.0  # 8 bits: sign plus 7-bit magnitude
    x = np.sign(y) * np.expm1(np.abs(y) * math.log1p(mu)) / mu
    return np.clip(x * 32768.0, -32768, 32767).astype(np.int16)


@dataclass
class LineStats:
    """Packet counts since the line was created; diff two snapshots for a window."""

    packets: int = 0
    lost: int = 0  # dropped by the network
    late: int = 0  # arrived after the jitter buffer gave up on them
    recovered: int = 0  # rebuilt from forward error correction
    concealed: int = 0  # filled by packet loss concealment
    silent: int = 0  # gaps the receiver played as silence

    def since(self, start: "LineStats") -> "LineStats":
        return LineStats(**{k: v - getattr(start, k) for k, v in asdict(self).items()})

    def snapshot(self) -> "LineStats":
        return LineStats(**asdict(self))


class PhoneLine:
    """Degrade caller audio like a phone call, in 20 ms packets.

    Output frames keep the input sample rate, so the STT stream and VAD see one
    consistent rate whichever line is selected.
    """

    def __init__(self, seed: int | None = None) -> None:
        self.line = "web"
        self.fixes = False
        self.stats = LineStats()
        self._rng = random.Random(seed)
        self._rate = 0
        self._down: rtc.AudioResampler | None = None
        self._up: rtc.AudioResampler | None = None
        self._pending = np.zeros(0, dtype=np.int16)
        self._bad = False
        self._last_good = np.zeros(PACKET, dtype=np.int16)
        self._fade = 0
        self._held: tuple[np.ndarray, bool] | None = None
        self._tape: deque[np.ndarray] = deque()
        self._tape_samples = 0
        self.samples_out = 0

    def set_line(self, line: str, fixes: bool) -> None:
        if line not in LINES:
            raise ValueError(f"Unknown line {line!r}")
        if line != self.line:
            # Start the codec path fresh so no audio from the old line leaks across.
            self._pending = np.zeros(0, dtype=np.int16)
            self._down = self._up = None
        if line != self.line or fixes != self.fixes:
            # A packet held for FEC under the old settings is stale now.
            self._held = None
            self._fade = 0
        self.line = line
        self.fixes = fixes

    # -- the network --

    def _arrives(self) -> tuple[bool, bool]:
        """Return (lost, late) for the next packet on the cell line."""
        if self._bad:
            self._bad = self._rng.random() >= TO_GOOD
        else:
            self._bad = self._rng.random() < TO_BAD
        lost = self._rng.random() < (LOSS_BAD if self._bad else LOSS_GOOD)
        delay = abs(self._rng.gauss(0.0, JITTER_SD))
        if self._rng.random() < SPIKE_CHANCE:
            delay += SPIKE_DELAY
        late = not lost and delay > (ADAPTIVE_BUFFER if self.fixes else STATIC_BUFFER)
        return lost, late

    def _conceal(self) -> np.ndarray:
        # Repeat the last good packet at falling volume; after about 60 ms there
        # is nothing honest left to repeat, so go quiet.
        self._fade += 1
        gain = max(0.0, 1.0 - 0.35 * self._fade)
        if gain == 0.0:
            self.stats.silent += 1
            return np.zeros(PACKET, dtype=np.int16)
        self.stats.concealed += 1
        return (self._last_good.astype(np.float32) * gain).astype(np.int16)

    def _receive(self, packet: np.ndarray, missing: bool) -> list[np.ndarray]:
        """Turn what the network delivered into the audio the receiver plays."""
        if not self.fixes:
            if missing:
                self.stats.silent += 1
                return [np.zeros(PACKET, dtype=np.int16)]
            return [packet]
        # With FEC, each packet also carries a copy of the one before it, so the
        # receiver waits one packet (20 ms) before deciding a packet is gone.
        held, self._held = self._held, (packet, missing)
        if held is None:
            return []
        previous, previous_missing = held
        if not previous_missing:
            out = previous
        elif not missing:
            self.stats.recovered += 1
            out = previous
        else:
            return [self._conceal()]
        self._last_good = out
        self._fade = 0
        return [out]

    def _transmit(self, packet: np.ndarray) -> list[np.ndarray]:
        self.stats.packets += 1
        packet = mu_law(packet)
        if self.line == "landline":
            return [packet]
        lost, late = self._arrives()
        self.stats.lost += lost
        self.stats.late += late
        return self._receive(packet, lost or late)

    # -- audio in, audio out --

    def process(self, frame: rtc.AudioFrame) -> list[rtc.AudioFrame]:
        if frame.num_channels != 1:
            raise ValueError("PhoneLine expects mono audio")
        if self.line == "web":
            self._record(np.frombuffer(frame.data, dtype=np.int16))
            return [frame]
        if self._down is None or self._rate != frame.sample_rate:
            self._rate = frame.sample_rate
            self._down = rtc.AudioResampler(self._rate, PHONE_RATE)
            self._up = rtc.AudioResampler(PHONE_RATE, self._rate)
        narrow = [np.frombuffer(f.data, dtype=np.int16) for f in self._down.push(frame)]
        self._pending = np.concatenate([self._pending, *narrow])
        played: list[np.ndarray] = []
        while len(self._pending) >= PACKET:
            packet, self._pending = self._pending[:PACKET], self._pending[PACKET:]
            played.extend(self._transmit(packet))
        if not played:
            return []
        audio = np.concatenate(played)
        out = self._up.push(rtc.AudioFrame(audio.tobytes(), PHONE_RATE, 1, len(audio)))
        for f in out:
            self._record(np.frombuffer(f.data, dtype=np.int16))
        return out

    def _record(self, samples: np.ndarray) -> None:
        if self._rate == 0:
            return
        self._tape.append(samples.copy())
        self._tape_samples += len(samples)
        self.samples_out += len(samples)
        while self._tape and self._tape_samples - len(self._tape[0]) >= TAPE_SECONDS * self._rate:
            self._tape_samples -= len(self._tape.popleft())

    def note_rate(self, rate: int) -> None:
        self._rate = self._rate or rate

    def clip(self, start: int, end: int) -> np.ndarray:
        """The audio the agent heard between two samples_out positions."""
        if end <= start:
            return np.zeros(0, dtype=np.int16)
        tape = np.concatenate(self._tape) if self._tape else np.zeros(0, dtype=np.int16)
        first = self.samples_out - len(tape)
        # Speech starts a little before voice activity detection notices it.
        start = max(start - int(PREROLL_SECONDS * self._rate), first)
        return tape[start - first : end - first]

    @property
    def rate(self) -> int:
        return self._rate

    @property
    def label(self) -> str:
        return LINE_LABELS[self.line]


class LineAudioInput(AudioInput):
    """Put a PhoneLine between the room's microphone track and the agent."""

    def __init__(self, line: PhoneLine, source: AudioInput) -> None:
        super().__init__(label="phone-line", source=source)
        self._line = line
        self._ready: deque[rtc.AudioFrame] = deque()

    async def __anext__(self) -> rtc.AudioFrame:
        while not self._ready:
            frame = await self.source.__anext__()
            self._line.note_rate(frame.sample_rate)
            self._ready.extend(self._line.process(frame))
        return self._ready.popleft()


def frames(samples: np.ndarray, rate: int, ms: int = 20) -> list[rtc.AudioFrame]:
    """Split int16 audio into frames for playback."""
    step = max(1, rate * ms // 1000)
    return [
        rtc.AudioFrame(chunk.tobytes(), rate, 1, len(chunk))
        for chunk in (samples[i : i + step] for i in range(0, len(samples), step))
        if len(chunk)
    ]
