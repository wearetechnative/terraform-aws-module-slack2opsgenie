import os
import json
import hmac
import time
import hashlib
import boto3
import urllib.request
import urllib.parse

sqs = boto3.client("sqs")

MAX_AGE_SECONDS = 60 * 5  # 5 minutes replay protection

# Warm-cache for channel names (persists across warm Lambda invocations)
_CHANNEL_CACHE = {}
_USER_CACHE = {}

def _get_header(headers: dict, name: str) -> str | None:
    if not headers:
        return None
    return headers.get(name) or headers.get(name.lower())


def _verify_slack_signature(headers: dict, raw_body: str) -> tuple[bool, str]:
    ts = _get_header(headers, "X-Slack-Request-Timestamp")
    sig = _get_header(headers, "X-Slack-Signature")
    if not ts or not sig:
        return False, "missing_slack_headers"

    try:
        ts_int = int(ts)
    except ValueError:
        return False, "bad_timestamp"

    if abs(int(time.time()) - ts_int) > MAX_AGE_SECONDS:
        return False, "timestamp_out_of_range"

    signing_secret = os.environ["SLACK_SIGNING_SECRET"].encode("utf-8")

    basestring = f"v0:{ts}:{raw_body}".encode("utf-8")
    digest = hmac.new(signing_secret, basestring, hashlib.sha256).hexdigest()
    expected = f"v0={digest}"

    if not hmac.compare_digest(expected, sig):
        return False, "signature_mismatch"

    return True, "ok"


def _slack_api_get(path: str, params: dict) -> dict:
    """
    Minimal Slack Web API GET helper.
    Requires env var BOT_USER_TOKEN (xoxb-...)
    """
    token = os.environ["BOT_USER_TOKEN"]
    qs = urllib.parse.urlencode(params)
    url = f"https://slack.com/api/{path}?{qs}"

    req = urllib.request.Request(
        url=url,
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    print(req)

    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_channel_name(channel_id: str) -> str:
    """
    Resolve channel ID -> channel name using conversations.info.
    Returns channel_id as fallback on any failure.
    """
    if not channel_id:
        return "unknown"

    if channel_id in _CHANNEL_CACHE:
        return _CHANNEL_CACHE[channel_id]

    try:
        data = _slack_api_get("conversations.info", {"channel": channel_id})
        
        print("conversations.info response:", json.dumps(data))
        if data.get("ok") and isinstance(data.get("channel"), dict):
            name = data["channel"].get("name") or channel_id
        else:
            # common error: missing_scope / not_in_channel / channel_not_found
            print(json.dumps({
                "level": "WARN",
                "msg": "Slack conversations.info not ok",
                "channel_id": channel_id,
                "error": data.get("error"),
            }))
            name = channel_id
    except Exception as e:
        print(json.dumps({
            "level": "WARN",
            "msg": "Slack conversations.info failed",
            "channel_id": channel_id,
            "error": str(e),
        }))
        name = channel_id

    _CHANNEL_CACHE[channel_id] = name
    return name

def get_user_name(user_id: str) -> str:
    """
    Resolve USER ID -> user name using users.info.
    Returns user_id as fallback on any failure.
    """
    if not user_id:
        return "unknown"

    if user_id in _USER_CACHE:
        return _USER_CACHE[user_id]

    try:
        data = _slack_api_get("users.info", {"user": user_id})
        
        print("users.info response:", json.dumps(data))
        if data.get("ok") and isinstance(data.get("user"), dict):
            name = data["user"].get("name") or user_id
        else:
            # common error: missing_scope / not_in_channel / channel_not_found
            print(json.dumps({
                "level": "WARN",
                "msg": "Slack users.info not ok",
                "user_id": user_id,
                "error": data.get("error"),
            }))
            name = user_id
    except Exception as e:
        print(json.dumps({
            "level": "WARN",
            "msg": "Slack users.info failed",
            "channel_id": user_id,
            "error": str(e),
        }))
        name = user_id

    _USER_CACHE[user_id] = name
    return name



def _build_direct_message_payload(slack_body: dict) -> dict:
    ev = slack_body.get("event", {})

    text = ev.get("text", "")
    
    channel_id = ev.get("channel", "unknown")
    channel_name = get_channel_name(channel_id)
    user_id = ev.get("user", "unknown")
    user_name = get_user_name(user_id)

    ts = ev.get("ts", "no-ts")
    event_id = slack_body.get("event_id") or f"{channel_id}-{ts}"

    client_name = os.getenv("CLIENT_NAME", "CustomerSlackChannel")
    sla = os.getenv("DEFAULT_SLA", "24x7")
    priority = os.getenv("DEFAULT_PRIORITY", "P1")

    readable_channel = f"#{channel_name}" if channel_name != channel_id else channel_id

    # This matches your SQS-consumer Lambda's "direct_message" branch expectations
    return {
        "direct_message": True,
        "subject": f"Slack message in {readable_channel} from {user_name}"[:100],
        "message": f"Channel: {readable_channel}\nUser: {user_name}\n\n{text}",
        "alias": f"slack-{event_id}",
        "client_name": client_name,
        "sla": sla,

        # Keep stable identifiers for correlation (even if name changes)
        "account_id": channel_id,
        "account_name": readable_channel,
        "priority": priority,
    }


def lambda_handler(event, context):
    headers = event.get("headers") or {}
    raw_body = event.get("body") or ""


    if event.get("isBase64Encoded"):
        return {"statusCode": 400, "body": "base64 not supported"}

    ok, reason = _verify_slack_signature(headers, raw_body)
    if not ok:
        print(json.dumps({"level": "ERROR", "msg": "Slack auth failed", "reason": reason}))
        return {
            "statusCode": 401,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "unauthorized", "reason": reason}),
        }

    body = json.loads(raw_body) if raw_body else {}

    # Slack URL verification
    if body.get("type") == "url_verification":
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"challenge": body.get("challenge")}),
        }

    if body.get("type") != "event_callback":
        return {"statusCode": 200, "body": "ok"}

    ev = body.get("event", {})

    # Only plain user messages (ignore edits, joins, bot posts, etc.)
    if ev.get("type") != "message":
        return {"statusCode": 200, "body": "ignored"}
    if ev.get("subtype"):
        return {"statusCode": 200, "body": "ignored"}
    if "bot_id" in ev:
        return {"statusCode": 200, "body": "ignored"}

    # OPTIONAL: lock to a specific channel id
    allowed_channel = os.getenv("ALLOWED_CHANNEL_ID")
    if allowed_channel and ev.get("channel") != allowed_channel:
        return {"statusCode": 200, "body": "ignored"}

    msg = _build_direct_message_payload(body)
    queue_url = os.environ["SQS_URL"]
    print(body)
    if "prio1" not in body["event"]["text"]:
        print("keyword not in message, skip sending to SQS")
    else:    
        sqs.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(msg),
        )

    print(json.dumps({
        "level": "INFO",
        "msg": "Enqueued to SQS",
        "alias": msg["alias"],
        "channel_id": msg["account_id"],
        "account_name": msg["account_name"],
        "contents": msg
        
    }))

    return {"statusCode": 200, "body": "ok"}