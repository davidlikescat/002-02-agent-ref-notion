#!/bin/bash
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
source ~/.zprofile 2>/dev/null
cd ~/.agent-ref-pipeline || exit 1
/usr/bin/python3 process_queue.py >> logs/processor.log 2>&1
