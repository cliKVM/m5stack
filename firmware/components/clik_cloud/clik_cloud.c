/* SPDX-License-Identifier: Apache-2.0 */
#include "clik_cloud.h"
#include "clik_usb.h"
#include "clik_board.h"
#include "sdkconfig.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <ctype.h>
#include <math.h>
#include "cJSON.h"
#include "esp_check.h"
#include "esp_crt_bundle.h"
#include "esp_http_client.h"
#include "esp_heap_caps.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "lwip/inet.h"

static const char *TAG = "clik_cloud";
static clik_cloud_config_t cfg;
static char session_id[65];
static char controller_session_id[65];
static uint32_t frame_counter, accepted_frame, usb_generation;
static unsigned last_http_status;
static char last_error_code[48];
unsigned clik_cloud_http_status(void) {
    return last_http_status;
}
bool clik_cloud_session_expired(void) {
    return !strcmp(last_error_code, "session_expired") ||
           !strcmp(last_error_code, "session_not_found");
}
static esp_err_t on_http_event(esp_http_client_event_t *e) {
    if (e->event_id == HTTP_EVENT_ON_HEADER && e->header_key && e->header_value &&
        !strcasecmp(e->header_key, "X-Clik-Error-Code"))
        snprintf(last_error_code, sizeof last_error_code, "%s", e->header_value);
    return ESP_OK;
}
static void random_key(char out[33]) {
    uint8_t b[16];
    esp_fill_random(b, sizeof b);
    for (int i = 0; i < 16; i++)
        snprintf(out + i * 2, 3, "%02x", b[i]);
}
static bool safe_id(const char *s) {
    if (!s || !*s)
        return false;
    for (; *s; s++)
        if (!isalnum((unsigned char)*s) && *s != '_' && *s != '-')
            return false;
    return true;
}
bool clik_cloud_url_allowed(const char *url) {
    if (!url || strlen(url) >= sizeof cfg.base_url || strchr(url, '@') || strchr(url, '?') ||
        strchr(url, '#'))
        return false;
    for (const char *p = url; *p; p++)
        if ((unsigned char)*p <= 32 || (unsigned char)*p > 126)
            return false;
    if (!strncmp(url, "https://", 8))
        return url[8] && url[8] != '/';
#if CONFIG_CLIK_ALLOW_HTTP_LAN
    if (!strncmp(url, "http://", 7)) {
        char host[16];
        size_t n = strcspn(url + 7, ":/");
        if (n == 0 || n >= sizeof host)
            return false;
        memcpy(host, url + 7, n);
        host[n] = 0;
        struct in_addr a;
        if (inet_pton(AF_INET, host, &a) != 1)
            return false;
        uint32_t v = ntohl(a.s_addr);
        return (v >> 24) == 10 || (v >> 20) == 0xAC1 || (v >> 16) == 0xC0A8;
    }
#endif
    return false;
}
esp_err_t clik_cloud_init(const clik_cloud_config_t *c) {
    if (!c || !clik_cloud_url_allowed(c->base_url) || !safe_id(c->device_id) ||
        strlen(c->device_token) < 24)
        return ESP_ERR_INVALID_ARG;
    cfg = *c;
    size_t n = strlen(cfg.base_url);
    while (n && cfg.base_url[n - 1] == '/')
        cfg.base_url[--n] = 0;
    clik_cloud_clear_session();
    return ESP_OK;
}
void clik_cloud_clear_session(void) {
    session_id[0] = 0;
    controller_session_id[0] = 0;
    frame_counter = accepted_frame = 0;
    usb_generation = 0;
}
const char *clik_cloud_session(void) {
    return session_id;
}
uint32_t clik_cloud_frame(void) {
    return accepted_frame;
}

static esp_http_client_handle_t client_new(const char *path, const char *content_type,
                                           const char *key) {
    last_http_status = 0;
    last_error_code[0] = 0;
    char url[512], auth[224];
    if (snprintf(url, sizeof url, "%s%s", cfg.base_url, path) >= (int)sizeof url)
        return NULL;
    snprintf(auth, sizeof auth, "Bearer %s", cfg.device_token);
    esp_http_client_config_t hc = {.url = url,
                                   .method = HTTP_METHOD_POST,
                                   .timeout_ms = strstr(path, "/audio/") ? 60000 : 10000,
                                   .crt_bundle_attach = esp_crt_bundle_attach,
                                   .buffer_size = 2048,
                                   .buffer_size_tx = 2048,
                                   .disable_auto_redirect = true,
                                   .event_handler = on_http_event};
    esp_http_client_handle_t h = esp_http_client_init(&hc);
    if (!h)
        return NULL;
    esp_http_client_set_header(h, "Authorization", auth);
    esp_http_client_set_header(h, "Content-Type", content_type);
    esp_http_client_set_header(h, "Accept", "application/json");
    esp_http_client_set_header(h, "Idempotency-Key", key);
    return h;
}
static esp_err_t write_all(esp_http_client_handle_t h, const void *data, size_t len) {
    const char *p = data;
    while (len) {
        int n = esp_http_client_write(h, p, len > 8192 ? 8192 : (int)len);
        if (n <= 0)
            return ESP_FAIL;
        p += n;
        len -= n;
    }
    return ESP_OK;
}
static esp_err_t response_json(esp_http_client_handle_t h, cJSON **out) {
    *out = NULL;
    if (esp_http_client_fetch_headers(h) < 0)
        return ESP_FAIL;
    last_http_status = esp_http_client_get_status_code(h);
    char *buf = malloc(16385);
    if (!buf)
        return ESP_ERR_NO_MEM;
    size_t used = 0;
    esp_err_t ret = ESP_OK;
    while (used < 16384) {
        int n = esp_http_client_read(h, buf + used, 16384 - used);
        if (n < 0) {
            ret = ESP_FAIL;
            break;
        }
        if (!n)
            break;
        used += n;
    }
    buf[used] = 0;
    if (!esp_http_client_is_complete_data_received(h))
        ret = ESP_ERR_INVALID_SIZE;
    if (last_http_status < 200 || last_http_status >= 300) {
        /* Error bodies can contain user text; log only HTTP status. */
        ESP_LOGW(TAG, "Gateway HTTP %u", last_http_status);
        ret = ESP_ERR_INVALID_RESPONSE;
    }
    if (ret == ESP_OK) {
        *out = cJSON_ParseWithLength(buf, used);
        if (!*out)
            ret = ESP_ERR_INVALID_RESPONSE;
    }
    free(buf);
    return ret;
}
static esp_err_t request(const char *path, const char *ct, const void *body, size_t len,
                         const char *key, cJSON **out) {
    *out = NULL;
    last_http_status = 0;
    esp_http_client_handle_t h = client_new(path, ct, key);
    if (!h)
        return ESP_ERR_NO_MEM;
    esp_err_t ret = esp_http_client_open(h, (int)len);
    if (ret == ESP_OK)
        ret = write_all(h, body, len);
    if (ret == ESP_OK)
        ret = response_json(h, out);
    esp_http_client_close(h);
    esp_http_client_cleanup(h);
    return ret;
}
static esp_err_t json_request(const char *path, const cJSON *body, const char *fixed_key,
                              cJSON **out) {
    char key[33];
    random_key(key);
    char *wire = cJSON_PrintUnformatted(body);
    if (!wire)
        return ESP_ERR_NO_MEM;
    esp_err_t ret =
        request(path, "application/json", wire, strlen(wire), fixed_key ? fixed_key : key, out);
    free(wire);
    return ret;
}
static bool get_str(const cJSON *j, const char *field, char *out, size_t cap) {
    const cJSON *v = cJSON_GetObjectItemCaseSensitive(j, field);
    if (!cJSON_IsString(v) || !v->valuestring || strlen(v->valuestring) >= cap)
        return false;
    strcpy(out, v->valuestring);
    return true;
}
static bool get_int(const cJSON *j, const char *field, int32_t *out, int32_t min, int32_t max) {
    const cJSON *v = cJSON_GetObjectItemCaseSensitive(j, field);
    if (!cJSON_IsNumber(v) || !isfinite(v->valuedouble) || v->valuedouble < min ||
        v->valuedouble > max || floor(v->valuedouble) != v->valuedouble)
        return false;
    *out = (int32_t)v->valuedouble;
    return true;
}
static void path_session(char *out, size_t n, const char *suffix) {
    snprintf(out, n, "/v1/sessions/%s/%s", session_id, suffix);
}
static void path_controller(char *out, size_t n, const char *suffix) {
    snprintf(out, n, "/v1/controllers/%s/%s", cfg.device_id, suffix);
}
static esp_err_t controller_empty_post(const char *suffix) {
    if (!controller_session_id[0])
        return ESP_ERR_INVALID_STATE;
    char path[192];
    path_controller(path, sizeof path, suffix);
    cJSON *j = cJSON_CreateObject(), *r = NULL;
    if (!j)
        return ESP_ERR_NO_MEM;
    cJSON_AddStringToObject(j, "expected_session_id", controller_session_id);
    esp_err_t ret = json_request(path, j, NULL, &r);
    cJSON_Delete(j);
    cJSON_Delete(r);
    return ret;
}
esp_err_t clik_cloud_controller_cancel(void) {
    return controller_empty_post("cancel");
}
esp_err_t clik_cloud_controller_target(clik_controller_target_t *target) {
    if (!target)
        return ESP_ERR_INVALID_ARG;
    memset(target, 0, sizeof *target);
    char path[192], key[33];
    path_controller(path, sizeof path, "target");
    random_key(key);
    esp_http_client_handle_t h = client_new(path, "application/json", key);
    if (!h)
        return ESP_ERR_NO_MEM;
    esp_http_client_set_method(h, HTTP_METHOD_GET);
    esp_err_t ret = esp_http_client_open(h, 0);
    cJSON *r = NULL;
    if (ret == ESP_OK)
        ret = response_json(h, &r);
    if (ret == ESP_OK) {
        char id[65];
        const cJSON *ready = cJSON_GetObjectItemCaseSensitive(r, "ready");
        if (!get_str(r, "controller_id", id, sizeof id) || strcmp(id, cfg.device_id) ||
            !cJSON_IsBool(ready) ||
            !get_str(r, "target_device_id", target->target_device_id,
                     sizeof target->target_device_id) ||
            !safe_id(target->target_device_id) ||
            !get_str(r, "state", target->state, sizeof target->state))
            ret = ESP_ERR_INVALID_RESPONSE;
        else {
            target->ready = cJSON_IsTrue(ready);
            get_str(r, "session_id", target->session_id, sizeof target->session_id);
            get_str(r, "platform", target->platform, sizeof target->platform);
            get_str(r, "transport", target->transport, sizeof target->transport);
            get_str(r, "observation_kind", target->observation_kind,
                    sizeof target->observation_kind);
            int32_t frame = 0;
            if (get_int(r, "latest_frame", &frame, 0, INT32_MAX))
                target->latest_frame = frame;
            if (target->ready && (!safe_id(target->session_id) || !target->observation_kind[0] ||
                                  !strcmp(target->observation_kind, "none")))
                ret = ESP_ERR_INVALID_RESPONSE;
        }
    }
    if (ret != ESP_OK)
        memset(target, 0, sizeof *target);
    snprintf(controller_session_id, sizeof controller_session_id, "%s", target->session_id);
    cJSON_Delete(r);
    esp_http_client_close(h);
    esp_http_client_cleanup(h);
    return ret;
}
esp_err_t clik_cloud_start_session(uint32_t generation) {
    clik_cloud_clear_session();
    char path[160], boot[33];
    random_key(boot);
    snprintf(path, sizeof path, "/v1/devices/%s/sessions", cfg.device_id);
    cJSON *j = cJSON_CreateObject(), *r = NULL, *caps = cJSON_AddArrayToObject(j, "capabilities");
    cJSON_AddStringToObject(j, "boot_id", boot);
    cJSON_AddBoolToObject(j, "simulated", false);
    cJSON_AddItemToArray(caps, cJSON_CreateString("adb"));
    cJSON_AddItemToArray(caps, cJSON_CreateString("aoa_hid"));
    cJSON_AddItemToArray(caps, cJSON_CreateString("display"));
    if (cfg.audio_available) {
        cJSON_AddItemToArray(caps, cJSON_CreateString("microphone"));
        cJSON_AddItemToArray(caps, cJSON_CreateString("speaker"));
    }
    esp_err_t ret = json_request(path, j, NULL, &r);
    if (ret == ESP_OK &&
        (!get_str(r, "session_id", session_id, sizeof session_id) || !safe_id(session_id)))
        ret = ESP_ERR_INVALID_RESPONSE;
    if (ret == ESP_OK)
        usb_generation = generation;
    else
        clik_cloud_clear_session();
    cJSON_Delete(j);
    cJSON_Delete(r);
    return ret;
}
#if CONFIG_CLIK_MODE_ANDROID_HOST
static esp_err_t capture_sink(const uint8_t *data, size_t length, void *context) {
    esp_http_client_handle_t h = context;
    char head[24];
    int n = snprintf(head, sizeof head, "%x\r\n", (unsigned)length);
    ESP_RETURN_ON_ERROR(write_all(h, head, n), TAG, "Chunk header");
    ESP_RETURN_ON_ERROR(write_all(h, data, length), TAG, "Chunk body");
    return write_all(h, "\r\n", 2);
}
#endif
esp_err_t clik_cloud_capture(void) {
#if !CONFIG_CLIK_MODE_ANDROID_HOST
    return controller_empty_post("capture");
#else
    if (!session_id[0])
        return ESP_ERR_INVALID_STATE;
    clik_usb_status_t us;
    clik_usb_get_status(&us);
    if (!us.adb_ready || us.connection_generation != usb_generation)
        return ESP_ERR_INVALID_STATE;
    char path[160], key[33], frame[24];
    path_session(path, sizeof path, "frames");
    random_key(key);
    uint32_t target = ++frame_counter;
    snprintf(frame, sizeof frame, "%lu", (unsigned long)target);
    esp_http_client_handle_t h = client_new(path, "image/png", key);
    if (!h)
        return ESP_ERR_NO_MEM;
    esp_http_client_set_header(h, "X-Frame-Id", frame);
    esp_err_t ret = esp_http_client_open(h, -1); /* HTTP/1.1 chunked */
    if (ret == ESP_OK)
        ret = clik_usb_capture(capture_sink, h, 45000);
    if (ret == ESP_OK)
        ret = write_all(h, "0\r\n\r\n", 5);
    cJSON *r = NULL;
    if (ret == ESP_OK)
        ret = response_json(h, &r);
    clik_usb_get_status(&us);
    if (ret == ESP_OK && us.connection_generation != usb_generation)
        ret = ESP_ERR_INVALID_STATE;
    int32_t actual = 0;
    if (ret == ESP_OK &&
        (!get_int(r, "frame_id", &actual, 1, INT32_MAX) || (uint32_t)actual != target))
        ret = ESP_ERR_INVALID_RESPONSE;
    if (ret == ESP_OK)
        accepted_frame = target;
    cJSON_Delete(r);
    esp_http_client_close(h);
    esp_http_client_cleanup(h);
    return ret;
#endif
}
esp_err_t clik_cloud_intent(const char *text) {
    if (!text || !*text)
        return ESP_ERR_INVALID_STATE;
    char path[192];
#if CONFIG_CLIK_MODE_ANDROID_HOST
    if (!session_id[0] || !accepted_frame)
        return ESP_ERR_INVALID_STATE;
    path_session(path, sizeof path, "intent");
#else
    if (!controller_session_id[0])
        return ESP_ERR_INVALID_STATE;
    path_controller(path, sizeof path, "intent");
#endif
    cJSON *j = cJSON_CreateObject(), *r = NULL;
    cJSON_AddStringToObject(j, "text", text);
#if CONFIG_CLIK_MODE_ANDROID_HOST
    cJSON_AddNumberToObject(j, "frame_id", accepted_frame);
#else
    cJSON_AddStringToObject(j, "expected_session_id", controller_session_id);
#endif
    esp_err_t ret = json_request(path, j, NULL, &r);
    cJSON_Delete(j);
    cJSON_Delete(r);
    return ret;
}
static bool parse_action(const cJSON *j, clik_cloud_action_t *out) {
    char type[24];
    if (!get_str(j, "type", type, sizeof type))
        return false;
    clik_action_t *a = &out->command;
    a->connection_generation = usb_generation;
    if (!strcmp(type, "screenshot")) {
        out->screenshot = true;
        return true;
    }
    if (!strcmp(type, "wait")) {
        out->wait = true;
        int32_t ms;
        if (!get_int(j, "duration_ms", &ms, 50, 5000))
            return false;
        out->wait_ms = ms;
        return true;
    }
    if (!strcmp(type, "tap")) {
        a->kind = CLIK_ACTION_TAP;
        return get_int(j, "x", &a->x, 0, 16383) && get_int(j, "y", &a->y, 0, 16383);
    }
    if (!strcmp(type, "swipe")) {
        a->kind = CLIK_ACTION_SWIPE;
        return get_int(j, "x", &a->x, 0, 16383) && get_int(j, "y", &a->y, 0, 16383) &&
               get_int(j, "x2", &a->x2, 0, 16383) && get_int(j, "y2", &a->y2, 0, 16383) &&
               get_int(j, "duration_ms", &a->duration_ms, 50, 3000);
    }
    if (!strcmp(type, "text")) {
        a->kind = CLIK_ACTION_TEXT;
        return get_str(j, "text", a->text, sizeof a->text);
    }
    if (!strcmp(type, "open_url")) {
        a->kind = CLIK_ACTION_OPEN_URL;
        return get_str(j, "url", a->text, sizeof a->text);
    }
    if (!strcmp(type, "key")) {
        static const struct {
            const char *name;
            int code;
        } keys[] = {{"HOME", 3},        {"BACK", 4},     {"ENTER", 66},      {"TAB", 61},
                    {"ESCAPE", 111},    {"DPAD_UP", 19}, {"DPAD_DOWN", 20},  {"DPAD_LEFT", 21},
                    {"DPAD_RIGHT", 22}, {"DEL", 67},     {"APP_SWITCH", 187}};
        char key[24];
        if (!get_str(j, "key", key, sizeof key))
            return false;
        for (size_t i = 0; i < sizeof keys / sizeof keys[0]; i++)
            if (!strcmp(key, keys[i].name)) {
                a->kind = CLIK_ACTION_KEY;
                a->keycode = keys[i].code;
                return true;
            }
    }
    return false;
}
esp_err_t clik_cloud_next(clik_cloud_action_t *out) {
    memset(out, 0, sizeof *out);
    if (!session_id[0])
        return ESP_ERR_INVALID_STATE;
    char path[160];
    path_session(path, sizeof path, "actions/next");
    int64_t request_started = esp_timer_get_time();
    cJSON *j = cJSON_CreateObject(), *r = NULL;
    esp_err_t ret = json_request(path, j, NULL, &r);
    cJSON_Delete(j);
    if (ret != ESP_OK) {
        cJSON_Delete(r);
        return ret;
    }
    char sid[65];
    if (!get_str(r, "session_id", sid, sizeof sid) || strcmp(sid, session_id)) {
        cJSON_Delete(r);
        return ESP_ERR_INVALID_RESPONSE;
    }
    get_str(r, "state", out->state, sizeof out->state);
    out->capture_requested = cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(r, "capture_requested"));
    const cJSON *a = cJSON_GetObjectItemCaseSensitive(r, "action");
    if (cJSON_IsObject(a)) {
        int32_t frame, ms;
        bool ok = get_str(a, "action_id", out->action_id, sizeof out->action_id) &&
                  safe_id(out->action_id) &&
                  get_str(a, "lease_id", out->lease_id, sizeof out->lease_id) &&
                  safe_id(out->lease_id) && get_int(a, "based_on_frame", &frame, 1, INT32_MAX) &&
                  get_int(a, "valid_for_ms", &ms, 1, 120000) &&
                  parse_action(cJSON_GetObjectItemCaseSensitive(a, "command"), out);
        if (!ok) {
            ret = ESP_ERR_INVALID_RESPONSE;
        } else {
            out->present = true;
            out->based_on_frame = frame;
            out->valid_for_ms = ms;
            out->received_at_us = request_started;
        }
    }
    cJSON_Delete(r);
    return ret;
}
esp_err_t clik_cloud_result(const clik_cloud_action_t *a, const char *status, const char *detail) {
    if (!session_id[0] || !a->action_id[0])
        return ESP_ERR_INVALID_STATE;
    char suffix[128], path[224], key[160];
    snprintf(suffix, sizeof suffix, "actions/%s/result", a->action_id);
    path_session(path, sizeof path, suffix);
    /* Same exact action-result retry has one deterministic idempotency key. */
    snprintf(key, sizeof key, "result:%s:%s", a->action_id, a->lease_id);
    cJSON *j = cJSON_CreateObject(), *r = NULL;
    cJSON_AddStringToObject(j, "lease_id", a->lease_id);
    cJSON_AddStringToObject(j, "status", status);
    cJSON_AddStringToObject(j, "detail", detail);
    esp_err_t ret = json_request(path, j, key, &r);
    cJSON_Delete(j);
    cJSON_Delete(r);
    return ret;
}
esp_err_t clik_cloud_stt(const uint8_t *wav, size_t length, char *text, size_t capacity) {
#if CONFIG_CLIK_MODE_ANDROID_HOST
    if (!session_id[0])
        return ESP_ERR_INVALID_STATE;
#endif
    char path[192], key[33];
    random_key(key);
#if CONFIG_CLIK_MODE_ANDROID_HOST
    path_session(path, sizeof path, "audio/stt");
#else
    path_controller(path, sizeof path, "audio/stt");
#endif
    cJSON *r = NULL;
    esp_err_t ret = request(path, "audio/wav", wav, length, key, &r);
    if (ret == ESP_OK && !get_str(r, "text", text, capacity))
        ret = ESP_ERR_INVALID_RESPONSE;
    cJSON_Delete(r);
    return ret;
}
static uint32_t le32(const uint8_t *p) {
    return p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
esp_err_t clik_cloud_speak(const char *text) {
    if (!text)
        return ESP_ERR_INVALID_STATE;
#if CONFIG_CLIK_MODE_ANDROID_HOST
    if (!session_id[0])
        return ESP_ERR_INVALID_STATE;
#endif
    char path[192], key[33];
    random_key(key);
#if CONFIG_CLIK_MODE_ANDROID_HOST
    path_session(path, sizeof path, "audio/tts");
#else
    path_controller(path, sizeof path, "audio/tts");
#endif
    cJSON *j = cJSON_CreateObject();
    cJSON_AddStringToObject(j, "text", text);
    char *body = cJSON_PrintUnformatted(j);
    cJSON_Delete(j);
    if (!body)
        return ESP_ERR_NO_MEM;
    esp_http_client_handle_t h = client_new(path, "application/json", key);
    if (!h) {
        free(body);
        return ESP_ERR_NO_MEM;
    }
    esp_http_client_set_header(h, "Accept", "audio/wav");
    esp_err_t ret = esp_http_client_open(h, strlen(body));
    if (ret == ESP_OK)
        ret = write_all(h, body, strlen(body));
    free(body);
    if (ret == ESP_OK && esp_http_client_fetch_headers(h) < 0)
        ret = ESP_FAIL;
    if (ret == ESP_OK && esp_http_client_get_status_code(h) != 200)
        ret = ESP_ERR_INVALID_RESPONSE;
    const size_t max = 1024 * 1024;
    uint8_t *wav = NULL;
    size_t used = 0;
    if (ret == ESP_OK) {
        wav = heap_caps_malloc(max, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (!wav)
            ret = ESP_ERR_NO_MEM;
    }
    while (ret == ESP_OK && used < max) {
        int n = esp_http_client_read(h, (char *)wav + used, max - used > 4096 ? 4096 : max - used);
        if (n < 0) {
            ret = ESP_FAIL;
            break;
        }
        if (!n)
            break;
        used += n;
    }
    if (ret == ESP_OK && !esp_http_client_is_complete_data_received(h))
        ret = ESP_ERR_INVALID_SIZE;
    /* Gateway normalizes to canonical 44-byte WAV / PCM16 mono 16kHz. */
    if (ret == ESP_OK &&
        (used < 44 || memcmp(wav, "RIFF", 4) || memcmp(wav + 8, "WAVEfmt ", 8) ||
         le32(wav + 16) != 16 || wav[20] != 1 || wav[21] != 0 || wav[22] != 1 || wav[23] != 0 ||
         le32(wav + 24) != 16000 || wav[34] != 16 || wav[35] != 0 || memcmp(wav + 36, "data", 4) ||
         le32(wav + 40) != used - 44 || ((used - 44) & 1)))
        ret = ESP_ERR_INVALID_RESPONSE;
    if (ret == ESP_OK)
        ret = clik_board_play((int16_t *)(wav + 44), (used - 44) / 2);
    free(wav);
    esp_http_client_close(h);
    esp_http_client_cleanup(h);
    return ret;
}
