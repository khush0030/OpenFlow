#!/bin/sh
# Build the prototype with plain swiftc (no Xcode project needed).
set -e
cd "$(dirname "$0")"
mkdir -p .build
xcrun swiftc -O -swift-version 5 -target arm64-apple-macos14 \
  -framework AppKit -framework SwiftUI \
  Sources/main.swift -o .build/flow-widget-proto
ls -l .build/flow-widget-proto
