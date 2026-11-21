/* SPDX-License-Identifier: Apache-2.0 */
#ifndef CLIK_PROTOCOL_H
#define CLIK_PROTOCOL_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

#define CLIK_ADB_MAX_PAYLOAD 4096u
#define CLIK_ADB_HEADER_SIZE 24u
#define CLIK_TEXT_MAX 1024u
#define CLIK_ADB_CNXN 0x4e584e43u
#define CLIK_ADB_AUTH 0x48545541u
#define CLIK_ADB_OPEN 0x4e45504fu
#define CLIK_ADB_OKAY 0x59414b4fu
#define CLIK_ADB_CLSE 0x45534c43u
#define CLIK_ADB_WRTE 0x45545257u
#define CLIK_ADB_VERSION 0x01000000u

typedef struct {
    uint32_t command, arg0, arg1, length, checksum, magic;
} clik_adb_header_t;
typedef int (*clik_adb_packet_cb)(const clik_adb_header_t *, const uint8_t *, void *);
typedef struct {
    uint8_t header[CLIK_ADB_HEADER_SIZE];
    uint8_t payload[CLIK_ADB_MAX_PAYLOAD];
    size_t header_used, payload_used;
    clik_adb_header_t current;
    bool failed;
} clik_adb_parser_t;
void clik_adb_parser_reset(clik_adb_parser_t *);
/* 0 success; -1 malformed/checksum/oversize; callback's nonzero result propagates. */
int clik_adb_feed(clik_adb_parser_t *, const uint8_t *, size_t, clik_adb_packet_cb, void *);
/* Size the next bulk IN read from the ADB header. Avoid waiting for a ZLP when
 * an exact-MPS payload is shorter than the allocated transfer buffer. */
size_t clik_adb_next_read_size(const clik_adb_parser_t *, uint16_t endpoint_mps);
uint32_t clik_adb_checksum(const uint8_t *, size_t);
void clik_adb_encode_header(uint8_t out[24], uint32_t command, uint32_t arg0,
                            uint32_t arg1, const uint8_t *payload, size_t length);

typedef struct {
    uint8_t interface_number, alternate_setting, ep_in, ep_out;
    uint16_t in_mps, out_mps;
} clik_adb_interface_t;
/* Strict length-bounded parser. Returns 1 found, 0 absent, -1 malformed. */
int clik_find_adb_interface(const uint8_t *config, size_t size, clik_adb_interface_t *);

typedef enum {
    CLIK_ACTION_TAP, CLIK_ACTION_SWIPE, CLIK_ACTION_KEY,
    CLIK_ACTION_TEXT, CLIK_ACTION_OPEN_URL
} clik_action_kind_t;
typedef struct {
    clik_action_kind_t kind;
    /* 0 permits current connection; otherwise rejects actions for stale phones. */
    uint32_t connection_generation;
    int32_t x, y, x2, y2, duration_ms, keycode;
    char text[CLIK_TEXT_MAX + 1];
} clik_action_t;
/* Builds the command after validating every parameter. No arbitrary shell API. */
int clik_action_command(const clik_action_t *, char *out, size_t out_size);

extern const uint8_t clik_keyboard_descriptor[];
extern const size_t clik_keyboard_descriptor_size;
extern const uint8_t clik_mouse_descriptor[];
extern const size_t clik_mouse_descriptor_size;
#ifdef __cplusplus
}
#endif
#endif
