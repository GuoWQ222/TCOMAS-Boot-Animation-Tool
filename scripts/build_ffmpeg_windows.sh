#!/usr/bin/env bash
# Build the separately bundled LGPL FFmpeg executable and static zlib.
# Run from a directory containing ffmpeg-8.1.1/ and zlib-1.3.1/.
# Requires a MinGW-w64 x86_64 GCC toolchain, GNU Make, Bash, and NASM.
set -euo pipefail

MAKE_BIN="${MAKE_BIN:-gmake}"
JOBS="${JOBS:-8}"
PATCH_BIN="${PATCH_BIN:-patch}"
ROOT_DIR="$(pwd)"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

cd "$ROOT_DIR/zlib-1.3.1"
"$MAKE_BIN" -f win32/Makefile.gcc -j"$JOBS" libz.a

cd "$ROOT_DIR/ffmpeg-8.1.1"
if ! grep -Fq '$(Q)$(file > $@.objs,$^)' ffbuild/library.mak; then
  "$PATCH_BIN" -p1 < "$SCRIPT_DIR/ffmpeg-response-files.patch"
fi
./configure \
  --target-os=mingw32 --arch=x86_64 \
  --cc=gcc --cxx=g++ --ar=ar --nm=nm --ranlib=ranlib --strip=strip \
  --disable-autodetect --disable-gpl --disable-nonfree --disable-version3 \
  --enable-static --disable-shared --enable-zlib --pkg-config=false \
  --disable-network --disable-avdevice --disable-ffplay --disable-ffprobe \
  --disable-doc --disable-debug \
  --extra-cflags="-I../zlib-1.3.1" \
  --extra-ldflags="-static -L../zlib-1.3.1"
"$MAKE_BIN" -j"$JOBS" ffmpeg.exe
