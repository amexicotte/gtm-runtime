"""Explicit Slack/Teams link replies for post-signature onboarding."""
import re
from urllib.parse import urlparse
def signed_client_message(company):
    return (f":tada: *{company} just signed!* The matched Deal is now *Won*.\n"
        "*Next step — client collaboration:* create the shared client space and reply "
        "in this thread with `slack: <channel URL>` or `teams: <channel URL>`. "
        "The agent will connect it to the Account and Client Project.")
def parse_channel_reply(text):
    text = re.sub(r"<([^>|]+)(?:\|[^>]+)?>", r"\1", str(text)).strip()
    match = re.match(r"^(slack|teams)\s*:\s*(https://\S+)", text, re.I)
    if not match: return None
    platform, url = match.group(1).title(), match.group(2).rstrip(").,;>")
    host = (urlparse(url).hostname or "").casefold()
    valid = (host == "slack.com" or host.endswith(".slack.com")) if platform == "Slack" else (
        host == "teams.microsoft.com" or host == "teams.live.com" or host.endswith(".sharepoint.com"))
    return (platform, url) if valid else None
