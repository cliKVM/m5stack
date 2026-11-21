/* SPDX-License-Identifier: Apache-2.0
 * Single-owner ESP-IDF USB client. AOA HID uses EP0; ADB uses a claimed bulk
 * interface. Transfer callbacks only signal completion. Protocol processing,
 * signing and network sinks run outside USB callbacks on the client task.
 */
#include "clik_usb.h"
#include "clik_adb_auth.h"
#include "esp_intr_alloc.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "usb/usb_host.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <inttypes.h>

static const char *TAG = "clik_usb";
typedef struct {
    usb_transfer_t *transfer;
    bool pending, done;
} transfer_slot_t;
typedef enum { REQUEST_KEY, REQUEST_MOUSE, REQUEST_ACTION, REQUEST_CAPTURE } request_kind_t;
typedef struct {
    request_kind_t kind;
    uint32_t timeout_ms;
    int64_t expires_us;
    esp_err_t result;
    SemaphoreHandle_t complete;
    uint8_t report[8];
    clik_action_t action;
    clik_usb_sink_t sink;
    void *sink_context;
} request_t;
typedef struct {
    uint32_t local_id, remote_id;
    bool done, capture, exit_seen;
    uint8_t exit_code;
    esp_err_t result;
    clik_usb_sink_t sink;
    void *sink_context;
    size_t bytes, signature_used, tail_used;
    uint8_t signature[8], tail[12];
    uint8_t shell_header[5];
    size_t shell_header_used;
    uint32_t shell_remaining;
    uint8_t shell_id;
} operation_t;
static struct {
    clik_usb_config_t config;
    clik_usb_status_t status;
    portMUX_TYPE status_lock;
    QueueHandle_t requests;
    SemaphoreHandle_t startup;
    SemaphoreHandle_t daemon_stopped;
    esp_err_t startup_result;
    usb_host_client_handle_t client;
    usb_device_handle_t device;
    TaskHandle_t client_task;
    TaskHandle_t daemon_task;
    volatile bool daemon_stop;
    bool installed;
    uint8_t pending_address;
    bool gone, claimed, started, auth_signature_sent, auth_public_sent, shell_v2, adb_broken;
    clik_adb_interface_t interface;
    transfer_slot_t control, tx, rx;
    clik_adb_parser_t parser;
    operation_t *operation;
    uint32_t next_local_id;
    int64_t transfer_deadline;
    int64_t last_mouse_report;
    uint8_t mouse_buttons;
} host = {.status_lock = portMUX_INITIALIZER_UNLOCKED};

static int64_t deadline_after(uint32_t ms) { return esp_timer_get_time() + (int64_t)ms * 1000; }
static uint32_t read32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
const char *clik_usb_state_name(clik_usb_state_t state) {
    switch (state) {
    case CLIK_USB_DISCONNECTED: return "disconnected";
    case CLIK_USB_CONNECTED: return "connected";
    case CLIK_USB_AOA_READY: return "aoa_ready";
    case CLIK_USB_ADB_AUTHORIZING: return "adb_authorizing";
    case CLIK_USB_ADB_READY: return "adb_ready";
    default: return "error";
    }
}
void clik_usb_get_status(clik_usb_status_t *out) {
    if (!out) return;
    portENTER_CRITICAL(&host.status_lock);
    *out = host.status;
    portEXIT_CRITICAL(&host.status_lock);
}
static void publish(clik_usb_status_t status) {
    portENTER_CRITICAL(&host.status_lock);
    bool changed = host.status.state != status.state ||
                   host.status.connection_generation != status.connection_generation ||
                   host.status.last_error != status.last_error;
    host.status = status;
    portEXIT_CRITICAL(&host.status_lock);
    if (changed) {
        ESP_LOGI(TAG, "state=%s generation=%" PRIu32 " vid=%04x pid=%04x aoa=%d adb=%d error=%s",
                 clik_usb_state_name(status.state), status.connection_generation,
                 status.vid, status.pid, status.aoa_ready, status.adb_ready,
                 esp_err_to_name(status.last_error));
    }
    if (host.config.on_status) host.config.on_status(&status, host.config.context);
}
static void state_error(esp_err_t error) {
    clik_usb_status_t status; clik_usb_get_status(&status);
    status.last_error = error; status.state = CLIK_USB_ERROR;
    publish(status);
}
static void client_event(const usb_host_client_event_msg_t *event, void *context) {
    (void)context;
    if (event->event == USB_HOST_CLIENT_EVENT_NEW_DEV) {
        if (!host.pending_address) host.pending_address = event->new_dev.address;
    } else if (event->event == USB_HOST_CLIENT_EVENT_DEV_GONE && event->dev_gone.dev_hdl == host.device) {
        host.gone = true;
    }
}
static void transfer_complete(usb_transfer_t *transfer) {
    transfer_slot_t *slot = transfer->context;
    slot->pending = false; slot->done = true;
}
static esp_err_t alloc_slot(transfer_slot_t *slot, size_t size) {
    esp_err_t result = usb_host_transfer_alloc(size, 0, &slot->transfer);
    if (result == ESP_OK) { slot->transfer->callback = transfer_complete; slot->transfer->context = slot; }
    return result;
}
static void pump(uint32_t ms) {
    usb_host_client_handle_events(host.client, pdMS_TO_TICKS(ms) ?: 1);
}
/* Never frees/reuses a transfer that has not completed cancellation. If EP0
 * cancellation fails, the slot stays pending until physical disconnect completes.
 */
static esp_err_t cancel_slot(transfer_slot_t *slot) {
    if (!slot->pending) return ESP_OK;
    usb_host_endpoint_halt(host.device, slot->transfer->bEndpointAddress);
    usb_host_endpoint_flush(host.device, slot->transfer->bEndpointAddress);
    int64_t until = deadline_after(1000);
    while (slot->pending && esp_timer_get_time() < until) pump(10);
    if (slot->pending) return ESP_ERR_TIMEOUT;
    if (!host.gone) usb_host_endpoint_clear(host.device, slot->transfer->bEndpointAddress);
    return ESP_OK;
}
static esp_err_t wait_slot(transfer_slot_t *slot, int64_t until, bool cancel_on_timeout) {
    while (slot->pending && !host.gone && esp_timer_get_time() < until) pump(10);
    if (host.gone) { cancel_slot(slot); return ESP_ERR_NOT_FOUND; }
    if (slot->pending) {
        if (cancel_on_timeout) cancel_slot(slot);
        return ESP_ERR_TIMEOUT;
    }
    if (!slot->done || slot->transfer->status != USB_TRANSFER_STATUS_COMPLETED) return ESP_FAIL;
    return ESP_OK;
}
static esp_err_t control_transfer(uint8_t type, uint8_t request, uint16_t value,
                                 uint16_t index, uint8_t *data, uint16_t length, int64_t until) {
    transfer_slot_t *slot = &host.control;
    if (!host.device || host.gone) return ESP_ERR_NOT_FOUND;
    if (slot->pending || length > 64) return ESP_ERR_INVALID_STATE;
    uint8_t *p = slot->transfer->data_buffer;
    p[0] = type; p[1] = request; p[2] = (uint8_t)value; p[3] = (uint8_t)(value >> 8);
    p[4] = (uint8_t)index; p[5] = (uint8_t)(index >> 8); p[6] = (uint8_t)length; p[7] = (uint8_t)(length >> 8);
    if (!(type & 0x80) && length) memcpy(p + 8, data, length);
    usb_transfer_t *t = slot->transfer;
    t->device_handle = host.device; t->bEndpointAddress = 0; t->num_bytes = 8 + length;
    t->flags = 0; slot->done = false; slot->pending = true;
    esp_err_t result = usb_host_transfer_submit_control(host.client, t);
    if (result != ESP_OK) { slot->pending = false; return result; }
    result = wait_slot(slot, until, true);
    if (result == ESP_OK && (type & 0x80) && length) {
        if (t->actual_num_bytes < 8 + length) return ESP_ERR_INVALID_SIZE;
        memcpy(data, p + 8, length);
    }
    if (result != ESP_OK && !slot->pending && !host.gone) usb_host_endpoint_clear(host.device, 0);
    return result;
}
static esp_err_t aoa_register(uint16_t id, const uint8_t *descriptor, size_t size) {
    esp_err_t e = control_transfer(0x40, 54, id, (uint16_t)size, NULL, 0, deadline_after(1500));
    if (e != ESP_OK) return e;
    for (size_t offset = 0; offset < size;) {
        size_t length = size - offset; if (length > 64) length = 64;
        e = control_transfer(0x40, 56, id, (uint16_t)offset, (uint8_t *)descriptor + offset,
                             (uint16_t)length, deadline_after(1500));
        if (e != ESP_OK) return e;
        offset += length;
    }
    return ESP_OK;
}
static esp_err_t aoa_init(void) {
    uint8_t version[2];
    esp_err_t result = control_transfer(0xc0, 51, 0, 0, version, 2, deadline_after(1500));
    if (result != ESP_OK || ((unsigned)version[0] | ((unsigned)version[1] << 8)) < 2) return ESP_ERR_NOT_SUPPORTED;
    result = aoa_register(1, clik_keyboard_descriptor, clik_keyboard_descriptor_size);
    if (result != ESP_OK) return result;
    result = aoa_register(2, clik_mouse_descriptor, clik_mouse_descriptor_size);
    if (result != ESP_OK) control_transfer(0x40, 55, 1, 0, NULL, 0, deadline_after(500));
    return result;
}
static esp_err_t send_bulk(const uint8_t *data, size_t size, int64_t until) {
    if (!host.claimed || host.gone || size > CLIK_ADB_MAX_PAYLOAD || host.tx.pending) return ESP_ERR_INVALID_STATE;
    usb_transfer_t *t = host.tx.transfer;
    memcpy(t->data_buffer, data, size);
    t->device_handle = host.device; t->bEndpointAddress = host.interface.ep_out;
    t->num_bytes = (int)size; t->flags = USB_TRANSFER_FLAG_ZERO_PACK;
    host.tx.done = false; host.tx.pending = true;
    esp_err_t result = usb_host_transfer_submit(t);
    if (result != ESP_OK) { host.tx.pending = false; return result; }
    return wait_slot(&host.tx, until, true);
}
static esp_err_t send_adb(uint32_t command, uint32_t a0, uint32_t a1, const uint8_t *data, size_t size) {
    uint8_t header[24];
    clik_adb_encode_header(header, command, a0, a1, data, size);
    int64_t until = deadline_after(3000);
    if (host.transfer_deadline && until > host.transfer_deadline) until = host.transfer_deadline;
    esp_err_t result = send_bulk(header, sizeof(header), until);
    if (result == ESP_OK && size) result = send_bulk(data, size, until);
    if (result != ESP_OK) host.adb_broken = true;
    return result;
}
static bool contains(const uint8_t *data, size_t size, const char *needle) {
    size_t length = strlen(needle);
    for (size_t i = 0; i + length <= size; ++i) if (!memcmp(data + i, needle, length)) return true;
    return false;
}
static esp_err_t capture_data(operation_t *op, const uint8_t *data, size_t size) {
    static const uint8_t png_signature[8] = {137,80,78,71,13,10,26,10};
    if (size > host.config.max_capture_bytes - op->bytes) return ESP_ERR_INVALID_SIZE;
    op->bytes += size;
    if (size >= sizeof(op->tail)) { memcpy(op->tail, data + size - sizeof(op->tail), sizeof(op->tail)); op->tail_used = sizeof(op->tail); }
    else {
        size_t keep = op->tail_used;
        if (keep + size > sizeof(op->tail)) keep = sizeof(op->tail) - size;
        memmove(op->tail, op->tail + op->tail_used - keep, keep);
        memcpy(op->tail + keep, data, size); op->tail_used = keep + size;
    }
    if (op->signature_used < 8) {
        size_t copy = 8 - op->signature_used; if (copy > size) copy = size;
        memcpy(op->signature + op->signature_used, data, copy);
        op->signature_used += copy; data += copy; size -= copy;
        if (op->signature_used < 8) return ESP_OK;
        if (memcmp(op->signature, png_signature, 8)) return ESP_ERR_INVALID_RESPONSE;
        esp_err_t result = op->sink(op->signature, 8, op->sink_context);
        if (result != ESP_OK) return result;
    }
    return size ? op->sink(data, size, op->sink_context) : ESP_OK;
}
/* shell_v2 frames may cross ADB packet boundaries. Only a received zero exit
 * status makes an action successful; a bare stream close is not success.
 */
static esp_err_t shell_data(operation_t *op, const uint8_t *data, size_t size) {
    while (size) {
        if (op->shell_header_used < 5) {
            size_t copy = 5 - op->shell_header_used; if (copy > size) copy = size;
            memcpy(op->shell_header + op->shell_header_used, data, copy);
            op->shell_header_used += copy; data += copy; size -= copy;
            if (op->shell_header_used < 5) continue;
            op->shell_id = op->shell_header[0]; op->shell_remaining = read32(op->shell_header + 1);
            if (op->exit_seen || op->shell_remaining > 1024 * 1024 || op->shell_id < 1 || op->shell_id > 3 ||
                (op->shell_id == 3 && op->shell_remaining != 1)) return ESP_ERR_INVALID_RESPONSE;
        }
        size_t take = op->shell_remaining; if (take > size) take = size;
        if (op->shell_id == 3 && take) { op->exit_code = data[0]; op->exit_seen = true; }
        data += take; size -= take; op->shell_remaining -= (uint32_t)take;
        if (!op->shell_remaining) op->shell_header_used = 0;
    }
    return ESP_OK;
}
static int packet_received(const clik_adb_header_t *h, const uint8_t *data, void *context) {
    (void)context;
    clik_usb_status_t status; clik_usb_get_status(&status);
    if (h->command == CLIK_ADB_AUTH) {
        if (h->arg0 != 1 || h->length != 20 || status.adb_ready) return ESP_ERR_INVALID_RESPONSE;
        if (!host.auth_signature_sent) {
            uint8_t signature[256];
            esp_err_t result = clik_adb_auth_sign(data, signature);
            if (result != ESP_OK) return result;
            host.auth_signature_sent = true;
            return send_adb(CLIK_ADB_AUTH, 2, 0, signature, sizeof(signature));
        }
        if (!host.auth_public_sent) {
            const char *public_key = clik_adb_auth_public_key();
            if (!public_key) return ESP_ERR_INVALID_STATE;
            host.auth_public_sent = true;
            status.state = CLIK_USB_ADB_AUTHORIZING; publish(status);
            return send_adb(CLIK_ADB_AUTH, 3, 0, (const uint8_t *)public_key, strlen(public_key) + 1);
        }
        return 0; /* User consent is handled on Android, never by injected input. */
    }
    if (h->command == CLIK_ADB_CNXN) {
        if (host.operation) return ESP_ERR_INVALID_RESPONSE;
        if (h->arg0 < CLIK_ADB_VERSION || h->arg1 < CLIK_ADB_MAX_PAYLOAD) return ESP_ERR_NOT_SUPPORTED;
        host.shell_v2 = contains(data, h->length, "shell_v2");
        status.adb_ready = true; status.state = CLIK_USB_ADB_READY; status.last_error = ESP_OK;
        publish(status); return 0;
    }
    operation_t *op = host.operation;
    if (!op || h->arg1 != op->local_id) {
        if (h->command == CLIK_ADB_WRTE || h->command == CLIK_ADB_OKAY)
            return send_adb(CLIK_ADB_CLSE, h->arg1, h->arg0, NULL, 0);
        return 0;
    }
    if (op->done && h->command != CLIK_ADB_CLSE)
        return send_adb(CLIK_ADB_CLSE, op->local_id, h->arg0, NULL, 0);
    if (h->command == CLIK_ADB_OKAY) {
        if (!h->arg0 || (op->remote_id && h->arg0 != op->remote_id)) return ESP_ERR_INVALID_RESPONSE;
        op->remote_id = h->arg0; return 0;
    }
    if (h->command == CLIK_ADB_WRTE) {
        if (!op->remote_id || h->arg0 != op->remote_id) return ESP_ERR_INVALID_RESPONSE;
        esp_err_t result = op->capture ? capture_data(op, data, h->length) : shell_data(op, data, h->length);
        if (result != ESP_OK) {
            op->result = result; op->done = true;
            return send_adb(CLIK_ADB_CLSE, op->local_id, op->remote_id, NULL, 0);
        }
        return send_adb(CLIK_ADB_OKAY, op->local_id, op->remote_id, NULL, 0);
    }
    if (h->command == CLIK_ADB_CLSE) {
        static const uint8_t iend[12] = {0,0,0,0,73,69,78,68,174,66,96,130};
        if (op->remote_id && h->arg0 != op->remote_id && h->arg0 != 0) return ESP_ERR_INVALID_RESPONSE;
        op->done = true;
        if (!op->remote_id) op->result = ESP_FAIL;
        else if (op->capture) {
            if (op->signature_used != 8 || op->tail_used != 12 || memcmp(op->tail, iend, 12)) op->result = ESP_ERR_INVALID_RESPONSE;
        } else if (!op->exit_seen || op->exit_code != 0 || op->shell_header_used) {
            op->result = ESP_FAIL;
            ESP_LOGW(TAG, "ADB action exit_seen=%d exit_code=%u incomplete_shell_frame=%d",
                     op->exit_seen, op->exit_code, op->shell_header_used != 0);
        }
        return op->remote_id ? send_adb(CLIK_ADB_CLSE, op->local_id, op->remote_id, NULL, 0) : 0;
    }
    return ESP_ERR_INVALID_RESPONSE;
}
static esp_err_t receive_adb(int64_t until) {
    transfer_slot_t *slot = &host.rx;
    if (!host.claimed || host.gone) return ESP_ERR_NOT_FOUND;
    if (!slot->pending && !slot->done) {
        usb_transfer_t *t = slot->transfer;
        t->device_handle = host.device; t->bEndpointAddress = host.interface.ep_in;
        size_t requested = clik_adb_next_read_size(&host.parser, host.interface.in_mps);
        if (!requested || requested > CLIK_ADB_MAX_PAYLOAD) return ESP_ERR_INVALID_STATE;
        t->num_bytes = (int)requested; t->flags = 0;
        slot->pending = true;
        esp_err_t result = usb_host_transfer_submit(t);
        if (result != ESP_OK) { slot->pending = false; return result; }
    }
    esp_err_t result = wait_slot(slot, until, false);
    if (result != ESP_OK) return result;
    slot->done = false;
    int parse = clik_adb_feed(&host.parser, slot->transfer->data_buffer,
                              (size_t)slot->transfer->actual_num_bytes, packet_received, NULL);
    return parse == -1 ? ESP_ERR_INVALID_RESPONSE : parse;
}
static void close_device(void) {
    if (!host.device) return;
    cancel_slot(&host.control); cancel_slot(&host.tx); cancel_slot(&host.rx);
    if (host.control.pending || host.tx.pending || host.rx.pending) return;
    if (host.claimed) {
        esp_err_t release = usb_host_interface_release(host.client, host.device, host.interface.interface_number);
        if (release != ESP_OK) { state_error(release); return; }
        host.claimed = false;
    }
    esp_err_t result = usb_host_device_close(host.client, host.device);
    if (result != ESP_OK) { state_error(result); return; }
    host.device = NULL; host.gone = false; host.control.done = host.tx.done = host.rx.done = false;
    host.auth_signature_sent = host.auth_public_sent = host.shell_v2 = host.adb_broken = false;
    host.mouse_buttons = 0;
    clik_adb_parser_reset(&host.parser);
    clik_usb_status_t old; clik_usb_get_status(&old);
    clik_usb_status_t status = {.state = CLIK_USB_DISCONNECTED, .connection_generation = old.connection_generation};
    publish(status);
}
static void open_device(uint8_t address) {
    esp_err_t result = usb_host_device_open(host.client, address, &host.device);
    if (result != ESP_OK) { state_error(result); return; }
    const usb_device_desc_t *descriptor = NULL;
    const usb_config_desc_t *configuration = NULL;
    result = usb_host_get_device_descriptor(host.device, &descriptor);
    if (result == ESP_OK) result = usb_host_get_active_config_descriptor(host.device, &configuration);
    if (result != ESP_OK) { host.gone = true; close_device(); return; }
    clik_usb_status_t status; clik_usb_get_status(&status);
    status = (clik_usb_status_t){.state=CLIK_USB_CONNECTED, .connected=true,
                                .connection_generation=status.connection_generation + 1,
                                .vid=descriptor->idVendor, .pid=descriptor->idProduct};
    publish(status);
    host.control.done = host.tx.done = host.rx.done = false;
    host.auth_signature_sent = host.auth_public_sent = host.shell_v2 = host.adb_broken = false;
    clik_adb_parser_reset(&host.parser);
    if (aoa_init() == ESP_OK) { status.aoa_ready = true; status.state = CLIK_USB_AOA_READY; publish(status); }
    if (host.gone || host.control.pending) return;
    int found = clik_find_adb_interface((const uint8_t *)configuration, configuration->wTotalLength, &host.interface);
    if (found != 1) { if (found < 0) state_error(ESP_ERR_INVALID_RESPONSE); return; }
    result = usb_host_interface_claim(host.client, host.device, host.interface.interface_number, host.interface.alternate_setting);
    if (result != ESP_OK) { state_error(result); return; }
    host.claimed = true;
    const char banner[] = "host::features=shell_v2;";
    result = send_adb(CLIK_ADB_CNXN, CLIK_ADB_VERSION, CLIK_ADB_MAX_PAYLOAD, (const uint8_t *)banner, sizeof(banner));
    if (result != ESP_OK) state_error(result);
    else { status.state = CLIK_USB_ADB_AUTHORIZING; publish(status); }
}
static esp_err_t run_adb_operation(request_t *request) {
    clik_usb_status_t status; clik_usb_get_status(&status);
    if (!status.adb_ready || !host.claimed || host.gone) return ESP_ERR_INVALID_STATE;
    if (request->kind == REQUEST_ACTION && request->action.connection_generation &&
        request->action.connection_generation != status.connection_generation) return ESP_ERR_INVALID_STATE;
    operation_t op = {.local_id = ++host.next_local_id, .capture=request->kind == REQUEST_CAPTURE,
                      .sink=request->sink, .sink_context=request->sink_context, .result=ESP_OK};
    if (!op.local_id) op.local_id = ++host.next_local_id;
    char service[CLIK_ADB_MAX_PAYLOAD];
    if (op.capture) strcpy(service, "exec:screencap -p");
    else {
        if (!host.shell_v2) return ESP_ERR_NOT_SUPPORTED;
        const char prefix[] = "shell,v2,raw:";
        memcpy(service, prefix, sizeof(prefix) - 1);
        if (clik_action_command(&request->action, service + sizeof(prefix) - 1, sizeof(service) - sizeof(prefix) + 1)) return ESP_ERR_INVALID_ARG;
    }
    host.operation = &op;
    host.transfer_deadline = request->expires_us;
    esp_err_t result = send_adb(CLIK_ADB_OPEN, op.local_id, 0, (const uint8_t *)service, strlen(service) + 1);
    int64_t until = request->expires_us;
    while (result == ESP_OK && !op.done && !host.gone && esp_timer_get_time() < until) {
        result = receive_adb(until);
        if (result == ESP_ERR_TIMEOUT && esp_timer_get_time() < until) result = ESP_OK;
    }
    if (host.gone) result = ESP_ERR_NOT_FOUND;
    else if (result == ESP_OK && !op.done) result = ESP_ERR_TIMEOUT;
    host.transfer_deadline = 0;
    if (!op.done && op.remote_id && !host.gone) send_adb(CLIK_ADB_CLSE, op.local_id, op.remote_id, NULL, 0);
    host.operation = NULL;
    if (result != ESP_OK && result != ESP_ERR_TIMEOUT && result != ESP_ERR_NOT_FOUND) host.adb_broken = true;
    return result == ESP_OK ? op.result : result;
}
static esp_err_t process_request(request_t *request) {
    if (esp_timer_get_time() >= request->expires_us) return ESP_ERR_TIMEOUT;
    clik_usb_status_t status; clik_usb_get_status(&status);
    if (!status.connected || host.gone) return ESP_ERR_NOT_FOUND;
    if (request->kind == REQUEST_ACTION || request->kind == REQUEST_CAPTURE) return run_adb_operation(request);
    if (!status.aoa_ready) return ESP_ERR_NOT_SUPPORTED;
    int64_t until = request->expires_us;
    if (request->kind == REQUEST_KEY) {
        esp_err_t press = control_transfer(0x40, 57, 1, 0, request->report, 8, until);
        uint8_t released[8] = {0};
        /* Release even when press fails: no stuck modifier after a partial failure. */
        esp_err_t release = control_transfer(0x40, 57, 1, 0, released, 8, deadline_after(500));
        return press == ESP_OK ? release : press;
    }
    esp_err_t result = control_transfer(0x40, 57, 2, 0, request->report, 4, until);
    if (result == ESP_OK) { host.mouse_buttons = request->report[0]; host.last_mouse_report = esp_timer_get_time(); }
    return result;
}
static void quarantine_adb(void) {
    clik_usb_status_t status; clik_usb_get_status(&status);
    if (status.adb_ready || status.state != CLIK_USB_ERROR) {
        status.adb_ready = false; status.state = CLIK_USB_ERROR;
        if (status.last_error == ESP_OK) status.last_error = ESP_ERR_INVALID_RESPONSE;
        publish(status);
    }
    if (!host.claimed) return;
    cancel_slot(&host.rx); cancel_slot(&host.tx);
    if (host.rx.pending || host.tx.pending) return;
    if (usb_host_interface_release(host.client, host.device, host.interface.interface_number) == ESP_OK) host.claimed = false;
}
static void daemon_task(void *context) {
    (void)context;
    for (;;) {
        uint32_t events = 0;
        usb_host_lib_handle_events(portMAX_DELAY, &events);
        if (events & USB_HOST_LIB_EVENT_FLAGS_NO_CLIENTS) usb_host_device_free_all();
        if (host.daemon_stop) break;
    }
    host.daemon_task = NULL;
    xSemaphoreGive(host.daemon_stopped);
    vTaskDelete(NULL);
}
static void client_task(void *context) {
    (void)context;
    usb_host_client_config_t config = {.is_synchronous=false, .max_num_event_msg=8,
        .async={.client_event_callback=client_event,.callback_arg=NULL}};
    esp_err_t result = usb_host_client_register(&config, &host.client);
    if (result == ESP_OK) result = alloc_slot(&host.control, 72);
    if (result == ESP_OK) result = alloc_slot(&host.tx, CLIK_ADB_MAX_PAYLOAD);
    if (result == ESP_OK) result = alloc_slot(&host.rx, CLIK_ADB_MAX_PAYLOAD);
    if (result != ESP_OK) {
        usb_host_transfer_free(host.control.transfer); host.control.transfer = NULL;
        usb_host_transfer_free(host.tx.transfer); host.tx.transfer = NULL;
        usb_host_transfer_free(host.rx.transfer); host.rx.transfer = NULL;
        if (host.client) { usb_host_client_deregister(host.client); host.client = NULL; }
        host.client_task = NULL;
    }
    host.startup_result = result;
    xSemaphoreGive(host.startup);
    if (result != ESP_OK) { state_error(result); vTaskDelete(NULL); return; }
    uint8_t addresses[1]; int count = 0;
    if (usb_host_device_addr_list_fill(1, addresses, &count) == ESP_OK && count) host.pending_address = addresses[0];
    for (;;) {
        pump(10);
        if (host.gone) close_device();
        if (!host.device && host.pending_address) {
            uint8_t address = host.pending_address; host.pending_address = 0; open_device(address);
        }
        request_t *request = NULL;
        if (xQueueReceive(host.requests, &request, 0) == pdTRUE) {
            request->result = process_request(request);
            xSemaphoreGive(request->complete);
        }
        if (host.device && host.mouse_buttons && !host.gone &&
            esp_timer_get_time() - host.last_mouse_report > 2000000) {
            uint8_t released[4] = {0};
            if (control_transfer(0x40, 57, 2, 0, released, 4, deadline_after(500)) == ESP_OK) host.mouse_buttons = 0;
        }
        if (host.device && host.adb_broken && !host.gone) quarantine_adb();
        else if (host.device && host.claimed && !host.gone) {
            result = receive_adb(deadline_after(10));
            if (result != ESP_OK && result != ESP_ERR_TIMEOUT && result != ESP_ERR_NOT_FOUND) {
                clik_usb_status_t status; clik_usb_get_status(&status);
                status.adb_ready = false; status.state = CLIK_USB_ERROR; status.last_error = result; publish(status);
                /* Quarantine malformed streams. Physical reconnect creates a clean
                 * USB generation and parser; do not keep acknowledging bad data. */
                host.adb_broken = true;
                quarantine_adb();
            }
        }
    }
}
/* Startup failure happens before any device is opened by our client. Drain
 * library devices, stop the daemon cooperatively, then uninstall. A rare driver
 * teardown failure remains installed/fail-closed and requires reboot. */
static void cleanup_start(void) {
    if (host.installed) {
        int64_t until = deadline_after(2000);
        while (usb_host_device_free_all() != ESP_OK && esp_timer_get_time() < until) {
            if (host.daemon_task) vTaskDelay(pdMS_TO_TICKS(10));
            else { uint32_t events; usb_host_lib_handle_events(pdMS_TO_TICKS(10), &events); }
        }
        if (host.daemon_task) {
            host.daemon_stop = true;
            usb_host_lib_unblock();
            if (xSemaphoreTake(host.daemon_stopped, pdMS_TO_TICKS(2000)) != pdTRUE) {
                state_error(ESP_ERR_TIMEOUT);
                return; /* Live task still owns its semaphore and library. */
            }
        }
        esp_err_t result = usb_host_uninstall();
        if (result == ESP_OK) host.installed = false;
        else state_error(result);
    }
    if (host.requests) { vQueueDelete(host.requests); host.requests = NULL; }
    if (host.startup) { vSemaphoreDelete(host.startup); host.startup = NULL; }
    if (host.daemon_stopped) { vSemaphoreDelete(host.daemon_stopped); host.daemon_stopped = NULL; }
    host.daemon_stop = false;
}
esp_err_t clik_usb_start(const clik_usb_config_t *config) {
    if (host.started || host.installed) return ESP_ERR_INVALID_STATE;
    if (config) host.config = *config;
    if (!host.config.max_capture_bytes) host.config.max_capture_bytes = 16 * 1024 * 1024;
    esp_err_t result = clik_adb_auth_init();
    if (result != ESP_OK) return result;
    host.requests = xQueueCreate(4, sizeof(request_t *));
    if (!host.requests) return ESP_ERR_NO_MEM;
    host.startup = xSemaphoreCreateBinary();
    host.daemon_stopped = xSemaphoreCreateBinary();
    if (!host.startup || !host.daemon_stopped) { cleanup_start(); return ESP_ERR_NO_MEM; }
    usb_host_config_t usb = {.skip_phy_setup=false, .intr_flags=ESP_INTR_FLAG_LEVEL1};
    result = usb_host_install(&usb);
    if (result != ESP_OK) { cleanup_start(); return result; }
    host.installed = true;
    if (xTaskCreate(daemon_task, "clik_usb_lib", 4096, NULL, 10, &host.daemon_task) != pdPASS) {
        cleanup_start(); return ESP_ERR_NO_MEM;
    }
    if (xTaskCreate(client_task, "clik_usb", 12288, NULL, 8, &host.client_task) != pdPASS) {
        cleanup_start(); return ESP_ERR_NO_MEM;
    }
    xSemaphoreTake(host.startup, portMAX_DELAY);
    vSemaphoreDelete(host.startup); host.startup = NULL;
    if (host.startup_result != ESP_OK) { cleanup_start(); return host.startup_result; }
    host.started = true;
    ESP_LOGI(TAG, "USB host started; AOA + ADB use one owner task; Android consent required");
    return ESP_OK;
}
static esp_err_t submit(request_t *request) {
    if (!host.started || !host.requests || xTaskGetCurrentTaskHandle() == host.client_task) return ESP_ERR_INVALID_STATE;
    if (!request->timeout_ms || request->timeout_ms > 120000) return ESP_ERR_INVALID_ARG;
    request->expires_us = deadline_after(request->timeout_ms);
    request->complete = xSemaphoreCreateBinary();
    if (!request->complete) return ESP_ERR_NO_MEM;
    if (xQueueSend(host.requests, &request, pdMS_TO_TICKS(request->timeout_ms)) != pdTRUE) {
        vSemaphoreDelete(request->complete); return ESP_ERR_TIMEOUT;
    }
    /* Worker owns borrowed callback context until completion. Timing out this wait
     * would allow use-after-return, so deadline is enforced by the USB operation. */
    xSemaphoreTake(request->complete, portMAX_DELAY);
    vSemaphoreDelete(request->complete);
    return request->result;
}
esp_err_t clik_usb_send_key(uint8_t modifier, uint8_t usage, uint32_t timeout_ms) {
    if (usage > 0x65) return ESP_ERR_INVALID_ARG;
    request_t *request = calloc(1, sizeof(*request)); if (!request) return ESP_ERR_NO_MEM;
    request->kind = REQUEST_KEY; request->timeout_ms = timeout_ms;
    request->report[0] = modifier; request->report[2] = usage;
    esp_err_t result = submit(request); free(request); return result;
}
esp_err_t clik_usb_send_mouse(uint8_t buttons, int8_t dx, int8_t dy, int8_t wheel, uint32_t timeout_ms) {
    if (buttons > 7 || dx == INT8_MIN || dy == INT8_MIN || wheel == INT8_MIN) return ESP_ERR_INVALID_ARG;
    request_t *request = calloc(1, sizeof(*request)); if (!request) return ESP_ERR_NO_MEM;
    request->kind = REQUEST_MOUSE; request->timeout_ms = timeout_ms;
    request->report[0] = buttons; request->report[1] = (uint8_t)dx; request->report[2] = (uint8_t)dy; request->report[3] = (uint8_t)wheel;
    esp_err_t result = submit(request); free(request); return result;
}
esp_err_t clik_usb_execute(const clik_action_t *action, uint32_t timeout_ms) {
    if (!action) return ESP_ERR_INVALID_ARG;
    request_t *request = calloc(1, sizeof(*request)); if (!request) return ESP_ERR_NO_MEM;
    request->kind = REQUEST_ACTION; request->timeout_ms = timeout_ms; request->action = *action;
    esp_err_t result = submit(request); free(request); return result;
}
esp_err_t clik_usb_capture(clik_usb_sink_t sink, void *context, uint32_t timeout_ms) {
    if (!sink) return ESP_ERR_INVALID_ARG;
    request_t *request = calloc(1, sizeof(*request)); if (!request) return ESP_ERR_NO_MEM;
    request->kind = REQUEST_CAPTURE; request->timeout_ms = timeout_ms; request->sink = sink; request->sink_context = context;
    esp_err_t result = submit(request); free(request); return result;
}
