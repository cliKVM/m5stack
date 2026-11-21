/* SPDX-License-Identifier: Apache-2.0 */
#include "clik_protocol.h"
#include <stdio.h>
#include <string.h>

static uint32_t le32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
static void put32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); p[2] = (uint8_t)(v >> 16); p[3] = (uint8_t)(v >> 24);
}
uint32_t clik_adb_checksum(const uint8_t *p, size_t n) {
    uint32_t sum = 0;
    for (size_t i = 0; i < n; ++i) sum += p[i];
    return sum;
}
void clik_adb_encode_header(uint8_t out[24], uint32_t command, uint32_t a0,
                            uint32_t a1, const uint8_t *p, size_t n) {
    put32(out, command); put32(out + 4, a0); put32(out + 8, a1);
    put32(out + 12, (uint32_t)n); put32(out + 16, clik_adb_checksum(p, n));
    put32(out + 20, command ^ 0xffffffffu);
}
void clik_adb_parser_reset(clik_adb_parser_t *p) { memset(p, 0, sizeof(*p)); }
size_t clik_adb_next_read_size(const clik_adb_parser_t *p, uint16_t mps) {
    if (!p || p->failed || (mps != 8 && mps != 16 && mps != 32 && mps != 64) || p->header_used > 24) return 0;
    size_t needed;
    if (p->header_used < 24) needed = 24 - p->header_used;
    else {
        if (p->current.length > CLIK_ADB_MAX_PAYLOAD || p->payload_used >= p->current.length) return 0;
        needed = p->current.length - p->payload_used;
    }
    return ((needed + mps - 1) / mps) * mps;
}
int clik_adb_feed(clik_adb_parser_t *p, const uint8_t *data, size_t size,
                  clik_adb_packet_cb cb, void *context) {
    if (!p || !cb || (!data && size) || p->failed) return -1;
    while (size) {
        if (p->header_used < 24) {
            size_t n = 24 - p->header_used;
            if (n > size) n = size;
            memcpy(p->header + p->header_used, data, n);
            p->header_used += n; data += n; size -= n;
            if (p->header_used < 24) continue;
            p->current = (clik_adb_header_t){
                le32(p->header), le32(p->header + 4), le32(p->header + 8),
                le32(p->header + 12), le32(p->header + 16), le32(p->header + 20)};
            if ((p->current.command ^ p->current.magic) != 0xffffffffu ||
                p->current.length > CLIK_ADB_MAX_PAYLOAD) { p->failed = true; return -1; }
        }
        size_t n = p->current.length - p->payload_used;
        if (n > size) n = size;
        if (n) memcpy(p->payload + p->payload_used, data, n);
        p->payload_used += n; data += n; size -= n;
        if (p->payload_used == p->current.length) {
            if (clik_adb_checksum(p->payload, p->payload_used) != p->current.checksum) {
                p->failed = true; return -1;
            }
            int result = cb(&p->current, p->payload, context);
            p->header_used = p->payload_used = 0;
            if (result) return result;
        }
    }
    return 0;
}

int clik_find_adb_interface(const uint8_t *p, size_t n, clik_adb_interface_t *out) {
    if (!p || !out || n < 9 || p[0] < 9 || p[1] != 2) return -1;
    size_t total = (size_t)p[2] | ((size_t)p[3] << 8);
    if (total < 9 || total > n || p[0] > total) return -1;
    bool adb = false;
    clik_adb_interface_t candidate = {0};
    for (size_t i = p[0]; i < total;) {
        if (i + 2 > total || p[i] < 2 || (size_t)p[i] > total - i) return -1;
        const uint8_t *d = p + i;
        if (d[1] == 4) {
            if (d[0] < 9) return -1;
            if (adb && candidate.ep_in && candidate.ep_out) { *out = candidate; return 1; }
            adb = d[5] == 0xff && d[6] == 0x42 && d[7] == 1;
            memset(&candidate, 0, sizeof(candidate));
            candidate.interface_number = d[2]; candidate.alternate_setting = d[3];
        } else if (d[1] == 5 && adb) {
            if (d[0] < 7) return -1;
            if ((d[3] & 3) == 2) {
                uint16_t mps = (uint16_t)d[4] | ((uint16_t)d[5] << 8);
                if ((mps != 8 && mps != 16 && mps != 32 && mps != 64) || (d[2] & 0x0f) == 0 || (d[2] & 0x70)) return -1;
                if (d[2] & 0x80) {
                    if (candidate.ep_in) return -1;
                    candidate.ep_in = d[2]; candidate.in_mps = mps;
                } else {
                    if (candidate.ep_out) return -1;
                    candidate.ep_out = d[2]; candidate.out_mps = mps;
                }
            }
        }
        i += d[0];
    }
    if (adb && candidate.ep_in && candidate.ep_out) { *out = candidate; return 1; }
    return 0;
}

static int coordinate(int32_t v) { return v >= 0 && v <= 32767; }
static int quote(const char *src, char *out, size_t cap, bool input_text) {
    size_t used = 0;
    if (cap < 3) return -1;
    out[used++] = '\'';
    for (size_t i = 0; src[i]; ++i) {
        unsigned char c = (unsigned char)src[i];
        if (c < 32 || c > 126 || (input_text && c == '%')) return -1;
        const char *replacement = NULL;
        if (c == '\'') replacement = "'\\''";
        else if (input_text && c == ' ') replacement = "%s";
        if (replacement) {
            size_t z = strlen(replacement);
            if (used + z + 2 > cap) return -1;
            memcpy(out + used, replacement, z); used += z;
        } else {
            if (used + 3 > cap) return -1;
            out[used++] = (char)c;
        }
    }
    out[used++] = '\''; out[used] = 0;
    return (int)used;
}
int clik_action_command(const clik_action_t *a, char *out, size_t n) {
    if (!a || !out || !n) return -1;
    out[0] = 0;
    int z = -1;
    switch (a->kind) {
    case CLIK_ACTION_TAP:
        if (!coordinate(a->x) || !coordinate(a->y)) return -1;
        z = snprintf(out, n, "input tap %ld %ld", (long)a->x, (long)a->y); break;
    case CLIK_ACTION_SWIPE:
        if (!coordinate(a->x) || !coordinate(a->y) || !coordinate(a->x2) || !coordinate(a->y2) ||
            a->duration_ms < 1 || a->duration_ms > 10000) return -1;
        z = snprintf(out, n, "input swipe %ld %ld %ld %ld %ld", (long)a->x, (long)a->y,
                     (long)a->x2, (long)a->y2, (long)a->duration_ms); break;
    case CLIK_ACTION_KEY:
        if (a->keycode < 1 || a->keycode > 400) return -1;
        z = snprintf(out, n, "input keyevent %ld", (long)a->keycode); break;
    case CLIK_ACTION_TEXT:
    case CLIK_ACTION_OPEN_URL: {
        if (!memchr(a->text, 0, sizeof(a->text)) || !a->text[0]) return -1;
        if (a->kind == CLIK_ACTION_OPEN_URL && strncmp(a->text, "https://", 8) && strncmp(a->text, "http://", 7)) return -1;
        const char *prefix = a->kind == CLIK_ACTION_TEXT ? "input text " : "am start -a android.intent.action.VIEW -d ";
        size_t prefix_n = strlen(prefix);
        if (prefix_n >= n) return -1;
        memcpy(out, prefix, prefix_n);
        z = quote(a->text, out + prefix_n, n - prefix_n, a->kind == CLIK_ACTION_TEXT);
        if (z < 0) { out[0] = 0; return -1; }
        z += (int)prefix_n; break;
    }
    default: return -1;
    }
    if (z < 0 || (size_t)z >= n) { out[0] = 0; return -1; }
    return 0;
}

/* HID boot keyboard and relative mouse. Independently authored descriptors. */
const uint8_t clik_keyboard_descriptor[] = {
    0x05,0x01,0x09,0x06,0xa1,0x01,0x05,0x07,0x19,0xe0,0x29,0xe7,0x15,0x00,
    0x25,0x01,0x75,0x01,0x95,0x08,0x81,0x02,0x95,0x01,0x75,0x08,0x81,0x01,
    0x95,0x06,0x75,0x08,0x15,0x00,0x25,0x65,0x19,0x00,0x29,0x65,0x81,0x00,0xc0
};
const size_t clik_keyboard_descriptor_size = sizeof(clik_keyboard_descriptor);
const uint8_t clik_mouse_descriptor[] = {
    0x05,0x01,0x09,0x02,0xa1,0x01,0x09,0x01,0xa1,0x00,0x05,0x09,0x19,0x01,
    0x29,0x03,0x15,0x00,0x25,0x01,0x95,0x03,0x75,0x01,0x81,0x02,0x95,0x01,
    0x75,0x05,0x81,0x01,0x05,0x01,0x09,0x30,0x09,0x31,0x09,0x38,0x15,0x81,
    0x25,0x7f,0x75,0x08,0x95,0x03,0x81,0x06,0xc0,0xc0
};
const size_t clik_mouse_descriptor_size = sizeof(clik_mouse_descriptor);
