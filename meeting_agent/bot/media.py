"""What the browser's media connections say about the meeting, on any platform.

Platforms redesign their pages; WebRTC stays the same. An init script wraps RTCPeerConnection in
every frame, so the bot can ask how many connections are up and how much meeting audio has
arrived. main.py uses this beside the page adapters: media flowing means the bot was admitted,
connections gone means the meeting is over for the bot, and no audio for a long time means
nobody is left to record.
"""

from dataclasses import dataclass

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

# A Proxy keeps the constructor's name, prototype and static methods, so pages see no change.
MEDIA_INIT_SCRIPT = """(() => {
    if (!window.RTCPeerConnection || window.__meetingAgentMedia) return;
    const connections = [];
    window.RTCPeerConnection = new Proxy(window.RTCPeerConnection, {
        construct(target, args, newTarget) {
            const connection = Reflect.construct(target, args, newTarget);
            connections.push(connection);
            return connection;
        },
    });
    window.__meetingAgentMedia = async () => {
        const state = { connections: 0, connected: 0, audioEnergy: 0, audioPackets: 0 };
        for (const connection of connections) {
            if (connection.connectionState === "closed") continue;
            state.connections += 1;
            if (connection.connectionState === "connected") state.connected += 1;
            try {
                (await connection.getStats()).forEach((report) => {
                    if (report.type === "inbound-rtp" && report.kind === "audio") {
                        state.audioEnergy += report.totalAudioEnergy || 0;
                        state.audioPackets += report.packetsReceived || 0;
                    }
                });
            } catch (error) {}
        }
        return state;
    };
})();"""

READ_STATE = "() => window.__meetingAgentMedia ? window.__meetingAgentMedia() : null"


@dataclass
class MediaState:
    connections: int = 0
    """Open connections; closed ones are not counted"""
    connected: int = 0
    audio_energy: float = 0.0
    """Grows with every sound received (WebRTC's totalAudioEnergy); silence adds about nothing"""
    audio_packets: int = 0

    @property
    def receiving_audio(self) -> bool:
        return self.connected > 0 and self.audio_packets > 0


async def media_state(page: Page) -> MediaState:
    """Adds up every frame: Zoom's web client runs its call inside an iframe."""
    total = MediaState()
    for frame in page.frames:
        try:
            state = await frame.evaluate(READ_STATE)
        except PlaywrightError:
            # A frame that is navigating or gone has nothing to report this time.
            continue
        if state:
            total.connections += state["connections"]
            total.connected += state["connected"]
            total.audio_energy += state["audioEnergy"]
            total.audio_packets += state["audioPackets"]
    return total
