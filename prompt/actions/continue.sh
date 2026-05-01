#!/usr/bin/env bash

action_continue_prompt() {
  echo "Unsupported SOURCE_ACTION: continue" >&2
  return 2
}
