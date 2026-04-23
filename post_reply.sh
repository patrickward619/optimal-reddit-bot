#!/bin/bash
# Usage: ./post_reply.sh "https://reddit.com/r/..." "Your reply text here"

source "$(dirname "$0")/.env"

THREAD_URL="$1"
CONTENT="$2"

if [ -z "$THREAD_URL" ] || [ -z "$CONTENT" ]; then
  echo "Usage: ./post_reply.sh <thread_url> <reply_content>"
  exit 1
fi

curl -s -X POST https://crowdreply.io/api/tasks \
  -H "x-api-key: $CROWDREPLY_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"taskType\": \"comment\",
    \"type\": \"RedditCommentTask\",
    \"platform\": \"reddit\",
    \"project\": \"$CROWDREPLY_PROJECT_ID\",
    \"content\": $(echo "$CONTENT" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'),
    \"threadUrl\": \"$THREAD_URL\"
  }"

echo ""
echo "✓ Posted to: $THREAD_URL"
