/* SPDX-License-Identifier: Apache-2.0 */
#include "clik_protocol.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static unsigned packets;
static size_t payload_size;
static uint8_t expected[CLIK_ADB_MAX_PAYLOAD];
static int accept_packet(const clik_adb_header_t *header, const uint8_t *data, void *context) {
    (void)context;
    assert(header->command == CLIK_ADB_WRTE);
    assert(header->arg0 == 7 && header->arg1 == 42);
    assert(header->length == payload_size);
    assert(!memcmp(data, expected, payload_size));
    packets++;
    return 0;
}
static int noop_packet(const clik_adb_header_t *h, const uint8_t *p, void *ctx) {
    (void)h; (void)p; (void)ctx; return 0;
}
static void packets_fragmentation(void) {
    uint8_t wire[24 + CLIK_ADB_MAX_PAYLOAD];
    for (size_t n = 0; n <= CLIK_ADB_MAX_PAYLOAD; n = n ? n * 2 : 1) {
        payload_size = n;
        for (size_t i = 0; i < n; ++i) expected[i] = (uint8_t)(i * 31 + 3);
        clik_adb_encode_header(wire, CLIK_ADB_WRTE, 7, 42, expected, n);
        memcpy(wire + 24, expected, n);
        for (size_t step = 1; step <= 137; step += 4) {
            clik_adb_parser_t parser; clik_adb_parser_reset(&parser); packets = 0;
            for (size_t i = 0; i < n + 24;) {
                size_t chunk = n + 24 - i; if (chunk > step) chunk = step;
                assert(clik_adb_feed(&parser, wire + i, chunk, accept_packet, NULL) == 0);
                i += chunk;
            }
            assert(packets == 1);
            assert(parser.header_used == 0 && parser.payload_used == 0);
        }
    }
    payload_size = 2; expected[0] = 1; expected[1] = 2;
    clik_adb_encode_header(wire, CLIK_ADB_WRTE, 7, 42, expected, 2);
    memcpy(wire + 24, expected, 2);
    memcpy(wire + 26, wire, 26);
    clik_adb_parser_t parser; clik_adb_parser_reset(&parser); packets = 0;
    assert(clik_adb_feed(&parser, wire, 52, accept_packet, NULL) == 0 && packets == 2);
}
static void corrupt_packets(void) {
    uint8_t wire[25], byte = 0xab;
    clik_adb_parser_t parser;
    clik_adb_encode_header(wire, CLIK_ADB_WRTE, 7, 42, &byte, 1); wire[24] = byte;
    wire[20] ^= 1; clik_adb_parser_reset(&parser);
    assert(clik_adb_feed(&parser, wire, sizeof(wire), noop_packet, NULL) == -1);
    assert(clik_adb_feed(&parser, wire, sizeof(wire), noop_packet, NULL) == -1);
    wire[20] ^= 1; wire[24] ^= 1; clik_adb_parser_reset(&parser);
    assert(clik_adb_feed(&parser, wire, sizeof(wire), noop_packet, NULL) == -1);
    clik_adb_encode_header(wire, CLIK_ADB_WRTE, 7, 42, NULL, 0);
    wire[12] = 1; wire[13] = 0x10; /* 4097: rejected before any payload copy. */
    clik_adb_parser_reset(&parser);
    assert(clik_adb_feed(&parser, wire, 24, noop_packet, NULL) == -1);
}
static void exact_packet_reads(void) {
    clik_adb_parser_t parser; clik_adb_parser_reset(&parser);
    uint8_t header[24], data[512] = {0};
    assert(clik_adb_next_read_size(&parser, 64) == 64);
    assert(clik_adb_next_read_size(&parser, 8) == 24);
    assert(clik_adb_next_read_size(&parser, 63) == 0);
    clik_adb_encode_header(header, CLIK_ADB_WRTE, 7, 42, data, sizeof(data));
    assert(clik_adb_feed(&parser, header, sizeof(header), noop_packet, NULL) == 0);
    assert(clik_adb_next_read_size(&parser, 64) == 512);
    assert(clik_adb_feed(&parser, data, 64, noop_packet, NULL) == 0);
    assert(clik_adb_next_read_size(&parser, 64) == 448);
    assert(clik_adb_feed(&parser, data + 64, 448, noop_packet, NULL) == 0);
    assert(clik_adb_next_read_size(&parser, 64) == 64);
    clik_adb_encode_header(header, CLIK_ADB_WRTE, 7, 42, data, 13);
    assert(clik_adb_feed(&parser, header, sizeof(header), noop_packet, NULL) == 0);
    assert(clik_adb_next_read_size(&parser, 64) == 64);
    assert(clik_adb_feed(&parser, data, 13, noop_packet, NULL) == 0);
}
static void usb_descriptors(void) {
    uint8_t config[] = {
        9,2,32,0,1,1,0,0x80,50,
        9,4,3,0,2,0xff,0x42,1,0,
        7,5,0x82,2,64,0,0,
        7,5,0x03,2,64,0,0
    };
    clik_adb_interface_t interface;
    assert(clik_find_adb_interface(config, sizeof(config), &interface) == 1);
    assert(interface.interface_number == 3 && interface.ep_in == 0x82 && interface.ep_out == 3);
    assert(interface.in_mps == 64 && interface.out_mps == 64);
    for (size_t i = 0; i < sizeof(config); ++i) assert(clik_find_adb_interface(config, i, &interface) == -1);
    config[14] = 3; assert(clik_find_adb_interface(config, sizeof(config), &interface) == 0); config[14] = 0xff;
    config[18] = 0; assert(clik_find_adb_interface(config, sizeof(config), &interface) == -1); config[18] = 7;
    config[27] = 0x83; assert(clik_find_adb_interface(config, sizeof(config), &interface) == -1); config[27] = 3;
    config[22] = 0; assert(clik_find_adb_interface(config, sizeof(config), &interface) == -1); config[22] = 64;
    config[0] = 255; assert(clik_find_adb_interface(config, sizeof(config), &interface) == -1);
}
static void actions_validation(void) {
    char command[4096];
    clik_action_t action = {.kind=CLIK_ACTION_TAP,.x=10,.y=20};
    assert(clik_action_command(&action, command, sizeof(command)) == 0);
    assert(!strcmp(command, "input tap 10 20"));
    action.x = -1; assert(clik_action_command(&action, command, sizeof(command)) == -1);
    action.x = 32768; assert(clik_action_command(&action, command, sizeof(command)) == -1);
    action = (clik_action_t){.kind=CLIK_ACTION_SWIPE,.x=1,.y=2,.x2=3,.y2=4,.duration_ms=250};
    assert(clik_action_command(&action, command, sizeof(command)) == 0);
    assert(!strcmp(command, "input swipe 1 2 3 4 250"));
    action.duration_ms = 0; assert(clik_action_command(&action, command, sizeof(command)) == -1);
    action = (clik_action_t){.kind=CLIK_ACTION_KEY,.keycode=3};
    assert(clik_action_command(&action, command, sizeof(command)) == 0);
    action.keycode = 0; assert(clik_action_command(&action, command, sizeof(command)) == -1);
    action = (clik_action_t){.kind=CLIK_ACTION_TEXT};
    strcpy(action.text, "hello world");
    assert(clik_action_command(&action, command, sizeof(command)) == 0);
    assert(!strcmp(command, "input text 'hello%sworld'"));
    strcpy(action.text, "a';echo owned;'");
    assert(clik_action_command(&action, command, sizeof(command)) == 0);
    assert(!strcmp(command, "input text 'a'\\'';echo%sowned;'\\'''"));
    strcpy(action.text, "bad\ncommand"); assert(clik_action_command(&action, command, sizeof(command)) == -1);
    strcpy(action.text, "100%s"); assert(clik_action_command(&action, command, sizeof(command)) == -1);
    strcpy(action.text, "\xc5\x82"); assert(clik_action_command(&action, command, sizeof(command)) == -1);
    memset(action.text, 'a', sizeof(action.text)); assert(clik_action_command(&action, command, sizeof(command)) == -1);
    action = (clik_action_t){.kind=CLIK_ACTION_OPEN_URL};
    strcpy(action.text, "javascript:alert(1)"); assert(clik_action_command(&action, command, sizeof(command)) == -1);
    strcpy(action.text, "https://get.heyasa.com/?a=1&b=2");
    assert(clik_action_command(&action, command, sizeof(command)) == 0);
    assert(!strcmp(command, "am start -a android.intent.action.VIEW -d 'https://get.heyasa.com/?a=1&b=2'"));
    assert(clik_action_command(&action, command, 10) == -1);
}
static void bounded_random_inputs(void) {
    uint32_t state = 0x12345678;
    uint8_t bytes[256];
    for (unsigned round = 0; round < 50000; ++round) {
        size_t size = round % sizeof(bytes);
        for (size_t i = 0; i < size; ++i) { state ^= state << 13; state ^= state >> 17; state ^= state << 5; bytes[i] = (uint8_t)state; }
        clik_adb_parser_t parser; clik_adb_parser_reset(&parser);
        clik_adb_feed(&parser, bytes, size, noop_packet, NULL);
        clik_adb_interface_t interface;
        clik_find_adb_interface(bytes, size, &interface);
    }
}
int main(void) {
    packets_fragmentation(); corrupt_packets(); exact_packet_reads(); usb_descriptors(); actions_validation(); bounded_random_inputs();
    puts("PASS: 6 groups; 490 fragmented roundtrips; exact-MPS reads; coalesced packets; checksum/magic/size rejection; USB descriptors; shell quoting; 50000 malformed inputs through both parsers");
    return 0;
}
