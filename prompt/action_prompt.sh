#!/usr/bin/env bash
# action_prompt.sh - dispatch worker prompts by SOURCE_ACTION.

PROMPT_DIR="${PROMPT_DIR:?PROMPT_DIR is required}"

source "$PROMPT_DIR/actions/spec.sh"
source "$PROMPT_DIR/actions/plan.sh"
source "$PROMPT_DIR/actions/implement.sh"
source "$PROMPT_DIR/actions/review.sh"
source "$PROMPT_DIR/actions/continue.sh"

build_action_prompt() {
  case "${SOURCE_ACTION:?SOURCE_ACTION is required}" in
    spec) action_spec_prompt ;;
    plan) action_plan_prompt ;;
    implement) action_implement_prompt ;;
    review) action_review_prompt ;;
    continue) action_continue_prompt ;;
    *)
      echo "Unsupported SOURCE_ACTION: ${SOURCE_ACTION}" >&2
      return 2
      ;;
  esac
}
