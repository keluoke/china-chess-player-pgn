#!/usr/bin/env python3
"""Retired shadow entrypoint; preserved only to reject stale invocations."""
def main():
    raise SystemExit("CLOUDFLARE_SHADOW_RETIRED: 影子链已归档退役；请使用 refresh.sh publish 推进生产发布。")
if __name__ == "__main__":
    main()
