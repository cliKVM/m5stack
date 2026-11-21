#!/usr/bin/env sh
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
output_dir=${CLIK_TEST_OUTPUT_DIR:-"$project_dir/build/native"}
mkdir -p "$output_dir"
${CC:-cc} -std=c11 -Wall -Wextra -Werror -pedantic -g -O1 \
    ${CLIK_SANITIZERS--fsanitize=address,undefined -fno-omit-frame-pointer} \
    -I"$project_dir/firmware/components/clik_protocol/include" \
    "$project_dir/firmware/components/clik_protocol/clik_protocol.c" \
    "$project_dir/tests/native/test_protocol.c" -o "$output_dir/test_protocol"
"$output_dir/test_protocol"
${CC:-cc} -std=c11 -Wall -Wextra -Werror -pedantic -O2 -fPIC -shared \
    -I"$project_dir/firmware/components/clik_protocol/include" \
    "$project_dir/firmware/components/clik_protocol/clik_protocol.c" \
    -o "$output_dir/libclik_protocol.so"
python3 "$project_dir/tests/native/check_shell_argv.py" "$output_dir/libclik_protocol.so"
