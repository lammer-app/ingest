#!/bin/bash
set -e
cd "$(dirname "$0")/site"
URL=$(vercel --prod --yes 2>&1 | grep -o 'https://investigative-pipeline-[^ ]*\.vercel\.app')
echo "Deployed: $URL"
vercel alias "$URL" lammer.app
echo "Aliased: lammer.app → $URL"
