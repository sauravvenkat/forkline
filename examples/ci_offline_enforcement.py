#!/usr/bin/env python3
"""
CI example: Offline mode enforcement.

Demonstrates Forkline's hard no-network guarantee:
1. Normal code can make network calls
2. Inside offline_context(), network calls fail immediately
3. The error is deterministic — same message every time
4. Normal access is restored after the context exits

No external dependencies. No actual network calls succeed.

Run:
    python examples/ci_offline_enforcement.py
"""

import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from forkline.ci.offline import (
    ForklineOfflineError,
    is_offline_mode,
    offline_context,
)


def main() -> None:
    print("=" * 60)
    print("Forkline CI: Offline Mode Enforcement")
    print("=" * 60)

    # Step 1: Outside offline mode — network functions are available
    print("\n1. Outside offline mode:")
    print(f"   is_offline_mode() = {is_offline_mode()}")
    print("   socket.getaddrinfo is available (not blocked)")

    # Step 2: Enter offline mode
    print("\n2. Entering offline_context()...")
    with offline_context():
        print(f"   is_offline_mode() = {is_offline_mode()}")

        # Try socket.connect
        print("\n3. Attempting socket.connect('example.com', 80)...")
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect(("example.com", 80))
            print("   ERROR: Should not reach here!")
        except ForklineOfflineError as e:
            print(f"   Blocked: {e}")

        # Try socket.create_connection
        print("\n4. Attempting socket.create_connection(('example.com', 443))...")
        try:
            socket.create_connection(("example.com", 443))
            print("   ERROR: Should not reach here!")
        except ForklineOfflineError as e:
            print(f"   Blocked: {e}")

        # Try DNS resolution
        print("\n5. Attempting socket.getaddrinfo('example.com', 80)...")
        try:
            socket.getaddrinfo("example.com", 80)
            print("   ERROR: Should not reach here!")
        except ForklineOfflineError as e:
            print(f"   Blocked: {e}")

        # Demonstrate determinism
        print("\n6. Verifying error determinism...")
        errors = []
        for _ in range(3):
            try:
                socket.create_connection(("api.openai.com", 443))
            except ForklineOfflineError as e:
                errors.append(str(e))
        assert len(set(errors)) == 1, "Errors should be identical"
        print("   All 3 errors identical: True")

    # Step 3: After context — network restored
    print("\n7. After offline_context():")
    print(f"   is_offline_mode() = {is_offline_mode()}")
    print("   Network functions restored.")

    print("\n" + "=" * 60)
    print("Offline enforcement demo complete.")
    print("=" * 60)


if __name__ == "__main__":
    main()
