"""How the bot's traffic leaves the host, set below the meeting page so every platform shares it.

- direct: the host's own address.
- proxy: Chromium's proxy, with WebRTC forced through it (see below).
- tunnel: the worker starts the container inside the Tailscale container's network, so all
  traffic, UDP included, already leaves through the exit node. Chromium needs nothing.
"""

import json
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from playwright.async_api import BrowserContext, ProxySettings

from ..enums import EgressMode

IP_ECHO_URL = "https://api.ipify.org?format=json"
STUN_URL = "stun:stun.l.google.com:19302"

# Gathers server-reflexive candidates: the address a STUN server saw this browser's UDP come from.
# That is the address the platform's media servers would see.
WEBRTC_PROBE = """async (stunUrl) => {
    const connection = new RTCPeerConnection({ iceServers: [{ urls: stunUrl }] });
    connection.createDataChannel("probe");
    const addresses = new Set();
    connection.onicecandidate = (event) => {
        if (event.candidate && event.candidate.type === "srflx") {
            addresses.add(event.candidate.address);
        }
    };
    await connection.setLocalDescription(await connection.createOffer());
    await new Promise((resolve) => {
        const timer = setTimeout(resolve, 5000);
        connection.onicegatheringstatechange = () => {
            if (connection.iceGatheringState === "complete") {
                clearTimeout(timer);
                resolve();
            }
        };
    });
    connection.close();
    return [...addresses];
}"""


@dataclass
class EgressCheck:
    http_ip: str
    webrtc_ips: list[str]

    @property
    def leaked_ips(self) -> list[str]:
        return [ip for ip in self.webrtc_ips if ip != self.http_ip]


def chromium_egress(mode: EgressMode, proxy_url: str) -> tuple[ProxySettings | None, list[str]]:
    if mode != EgressMode.PROXY:
        return None, []
    if not proxy_url:
        raise ValueError("Egress mode is proxy, but no proxy URL is set (EGRESS_PROXY_URL)")
    parts = urlsplit(proxy_url)
    proxy: ProxySettings = {"server": f"{parts.scheme}://{parts.hostname}:{parts.port}"}
    if parts.username:
        proxy["username"] = unquote(parts.username)
        proxy["password"] = unquote(parts.password or "")
    # Chromium's proxy carries TCP only. Without this, WebRTC sends media over UDP straight out
    # and the platform sees the host's address. With it, media goes over TCP through the proxy.
    return proxy, ["--force-webrtc-ip-handling-policy=disable_non_proxied_udp"]


async def check_egress(context: BrowserContext) -> EgressCheck:
    page = await context.new_page()
    try:
        await page.goto(IP_ECHO_URL)
        http_ip = json.loads(await page.inner_text("body"))["ip"]
        webrtc_ips = await page.evaluate(WEBRTC_PROBE, STUN_URL)
    finally:
        await page.close()
    return EgressCheck(http_ip=http_ip, webrtc_ips=webrtc_ips)
