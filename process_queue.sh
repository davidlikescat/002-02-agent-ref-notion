#!/bin/bash
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
source ~/.zprofile 2>/dev/null
cd ~/.agent-ref-pipeline || exit 1
/opt/homebrew/bin/python3.13 process_queue.py >> logs/processor.log 2>&1
