/* SPDX-License-Identifier: Apache-2.0 */
#include "app.h"
#include "clik_board.h"
#include "clik_usb.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "cJSON.h"
#include "esp_check.h"
#include "esp_event.h"
#include "esp_http_server.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_random.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "nvs.h"
#include "lwip/ip4_addr.h"
#include "esp_sntp.h"

static const char *TAG = "clik_setup";
static EventGroupHandle_t net_events;
static bool provisioning;
static unsigned retries;
static char ip[20] = "0.0.0.0", local_key[13], ap_name[24];
static httpd_handle_t server;
extern const unsigned char local_html_start[] asm("_binary_local_html_start");
extern const unsigned char local_html_end[] asm("_binary_local_html_end");
bool clik_wifi_ready(void) {
    return net_events && (xEventGroupGetBits(net_events) & 1);
}
const char *clik_ip(void) {
    return ip;
}
const char *clik_control_key(void) {
    return local_key;
}
static esp_err_t send_json(httpd_req_t *r, int status, cJSON *j) {
    const char *st = status == 200   ? "200 OK"
                     : status == 202 ? "202 Accepted"
                     : status == 401 ? "401 Unauthorized"
                     : status == 409 ? "409 Conflict"
                                     : "400 Bad Request";
    httpd_resp_set_status(r, st);
    httpd_resp_set_type(r, "application/json");
    httpd_resp_set_hdr(r, "Cache-Control", "no-store");
    char *s = cJSON_PrintUnformatted(j);
    cJSON_Delete(j);
    if (!s)
        return ESP_ERR_NO_MEM;
    esp_err_t ret = httpd_resp_send(r, s, HTTPD_RESP_USE_STRLEN);
    free(s);
    return ret;
}
static esp_err_t answer(httpd_req_t *r, int status, const char *msg) {
    cJSON *j = cJSON_CreateObject();
    cJSON_AddStringToObject(j, status < 300 ? "status" : "error", msg);
    return send_json(r, status, j);
}
static bool authenticated(httpd_req_t *r) {
    char key[32], ct[40];
    if (httpd_req_get_hdr_value_str(r, "X-Control-Key", key, sizeof key) != ESP_OK ||
        strlen(key) != 12)
        return false;
    if (httpd_req_get_hdr_value_str(r, "Content-Type", ct, sizeof ct) != ESP_OK ||
        strncmp(ct, "application/json", 16))
        return false;
    unsigned diff = 0;
    for (int i = 0; i < 12; i++)
        diff |= (unsigned char)key[i] ^ (unsigned char)local_key[i];
    return diff == 0;
}
static cJSON *read_json(httpd_req_t *r) {
    if (r->content_len < 2 || r->content_len > 4096)
        return NULL;
    char *b = malloc(r->content_len + 1);
    if (!b)
        return NULL;
    size_t n = 0;
    while (n < r->content_len) {
        int got = httpd_req_recv(r, b + n, r->content_len - n);
        if (got <= 0) {
            free(b);
            return NULL;
        }
        n += got;
    }
    b[n] = 0;
    cJSON *j = cJSON_ParseWithLength(b, n);
    free(b);
    return j;
}
static bool copy_string(cJSON *j, const char *name, char *out, size_t cap, bool empty) {
    cJSON *v = cJSON_GetObjectItemCaseSensitive(j, name);
    if (!cJSON_IsString(v) || strlen(v->valuestring) >= cap || (!empty && !v->valuestring[0]))
        return false;
    strcpy(out, v->valuestring);
    return true;
}
static bool number(cJSON *j, const char *name, int *out, int lo, int hi) {
    cJSON *v = cJSON_GetObjectItemCaseSensitive(j, name);
    if (!cJSON_IsNumber(v) || !isfinite(v->valuedouble) ||
        floor(v->valuedouble) != v->valuedouble || v->valuedouble < lo || v->valuedouble > hi)
        return false;
    *out = (int)v->valuedouble;
    return true;
}
esp_err_t clik_load_settings(clik_settings_t *s) {
    memset(s, 0, sizeof *s);
    nvs_handle_t h;
    esp_err_t ret = nvs_open("clik_config", NVS_READONLY, &h);
    if (ret != ESP_OK)
        return ret;
    size_t n = sizeof s->ssid;
    ret = nvs_get_str(h, "ssid", s->ssid, &n);
#define READ_SETTING(key, field)                                                                   \
    do {                                                                                           \
        n = sizeof(field);                                                                         \
        if (ret == ESP_OK)                                                                         \
            ret = nvs_get_str(h, key, field, &n);                                                  \
    } while (0)
    READ_SETTING("password", s->password);
    READ_SETTING("url", s->cloud.base_url);
    READ_SETTING("device", s->cloud.device_id);
    READ_SETTING("token", s->cloud.device_token);
#undef READ_SETTING
    nvs_close(h);
    return ret;
}
static void reboot_task(void *unused) {
    (void)unused;
    vTaskDelay(pdMS_TO_TICKS(1200));
    esp_restart();
}
static esp_err_t configure(httpd_req_t *r) {
    if (!provisioning)
        return answer(r, 409, "Konfiguracja dostępna tylko w trybie setup");
    if (!authenticated(r))
        return answer(r, 401, "Wpisz kod z ekranu");
    cJSON *j = read_json(r);
    clik_settings_t s = {0};
    bool valid =
        j && copy_string(j, "ssid", s.ssid, sizeof s.ssid, false) &&
        copy_string(j, "password", s.password, sizeof s.password, true) &&
        copy_string(j, "base_url", s.cloud.base_url, sizeof s.cloud.base_url, false) &&
        copy_string(j, "device_id", s.cloud.device_id, sizeof s.cloud.device_id, false) &&
        copy_string(j, "device_token", s.cloud.device_token, sizeof s.cloud.device_token, false) &&
        clik_cloud_init(&s.cloud) == ESP_OK;
    cJSON_Delete(j);
    if (!valid)
        return answer(r, 400, "Nieprawidłowe dane, URL lub token (min. 24 znaki)");
    nvs_handle_t h;
    esp_err_t e = nvs_open("clik_config", NVS_READWRITE, &h);
    if (e != ESP_OK)
        return answer(r, 400, "Nie można zapisać konfiguracji");
#define SAVE(key, field)                                                                           \
    do {                                                                                           \
        if (e == ESP_OK)                                                                           \
            e = nvs_set_str(h, key, field);                                                        \
    } while (0)
    SAVE("ssid", s.ssid);
    SAVE("password", s.password);
    SAVE("url", s.cloud.base_url);
    SAVE("device", s.cloud.device_id);
    SAVE("token", s.cloud.device_token);
#undef SAVE
    if (e == ESP_OK)
        e = nvs_commit(h);
    nvs_close(h);
    memset(&s, 0, sizeof s);
    if (e != ESP_OK)
        return answer(r, 400, "Błąd zapisu NVS");
    esp_err_t ret = answer(r, 202, "Zapisano. Kontroler uruchomi się ponownie.");
    xTaskCreate(reboot_task, "reboot", 2048, NULL, 2, NULL);
    return ret;
}
static esp_err_t status(httpd_req_t *r) {
    clik_usb_status_t s = {0};
    clik_usb_get_status(&s);
    cJSON *j = cJSON_CreateObject();
    cJSON_AddBoolToObject(j, "provisioning", provisioning);
    cJSON_AddBoolToObject(j, "wifi", clik_wifi_ready());
    cJSON_AddBoolToObject(j, "host_started", clik_app_host_started());
    cJSON_AddBoolToObject(j, "paused", clik_app_paused());
    cJSON_AddStringToObject(j, "board", clik_board_name());
    cJSON_AddStringToObject(j, "ip", ip);
    cJSON_AddStringToObject(j, "usb", clik_usb_state_name(s.state));
    cJSON_AddBoolToObject(j, "aoa", s.aoa_ready);
    cJSON_AddBoolToObject(j, "adb", s.adb_ready);
    return send_json(r, 200, j);
}
static esp_err_t km(httpd_req_t *r) {
    if (!authenticated(r))
        return answer(r, 401, "Wpisz kod z ekranu");
    if (!clik_app_host_started() || clik_app_paused())
        return answer(r, 409, "Uruchom host fizycznym przyciskiem kontrolera");
    cJSON *j = read_json(r);
    char type[20];
    esp_err_t e = ESP_ERR_INVALID_ARG;
    if (j && copy_string(j, "type", type, sizeof type, false)) {
        if (!strcmp(type, "key")) {
            int usage, mod;
            if (number(j, "usage", &usage, 0, 101) && number(j, "modifier", &mod, 0, 255))
                e = clik_usb_send_key(mod, usage, 3000);
        } else if (!strcmp(type, "mouse")) {
            int x, y, w;
            if (number(j, "dx", &x, -100, 100) && number(j, "dy", &y, -100, 100) &&
                number(j, "wheel", &w, -10, 10))
                e = clik_usb_send_mouse(0, x, y, w, 3000);
        } else if (!strcmp(type, "click")) {
            e = clik_usb_send_mouse(1, 0, 0, 0, 3000);
            esp_err_t release = clik_usb_send_mouse(0, 0, 0, 0, 3000);
            if (e == ESP_OK)
                e = release;
        }
    }
    cJSON_Delete(j);
    return answer(r, e == ESP_OK ? 200 : 400, e == ESP_OK ? "OK" : esp_err_to_name(e));
}
static esp_err_t request_action(httpd_req_t *r) {
    if (!authenticated(r))
        return answer(r, 401, "Wpisz kod z ekranu");
    cJSON *j = read_json(r);
    char type[20];
    user_request_t q = {0};
    bool ok = j && copy_string(j, "type", type, sizeof type, false);
    if (ok) {
        if (!strcmp(type, "capture"))
            q.kind = USER_CAPTURE;
        else if (!strcmp(type, "voice"))
            q.kind = USER_VOICE;
        else if (!strcmp(type, "pause"))
            q.kind = USER_PAUSE;
        else if (!strcmp(type, "resume"))
            q.kind = USER_RESUME;
        else if (!strcmp(type, "intent")) {
            q.kind = USER_INTENT;
            ok = copy_string(j, "text", q.text, sizeof q.text, false);
        } else
            ok = false;
    }
    cJSON_Delete(j);
    if (!ok)
        return answer(r, 400, "Nieprawidłowe polecenie");
    if (!clik_app_enqueue(&q))
        return answer(r, 409, "Kolejka pełna, spróbuj ponownie");
    return answer(r, 202, "Przyjęto");
}
static esp_err_t home(httpd_req_t *r) {
    httpd_resp_set_type(r, "text/html; charset=utf-8");
    httpd_resp_set_hdr(r, "Cache-Control", "no-store");
    httpd_resp_set_hdr(r, "X-Frame-Options", "DENY");
    return httpd_resp_send(r, (const char *)local_html_start,
                           local_html_end - local_html_start - 1);
}
void clik_local_server_start(void) {
    if (server)
        return;
    httpd_config_t c = HTTPD_DEFAULT_CONFIG();
    c.stack_size = 6144;
    c.max_uri_handlers = 8;
    c.recv_wait_timeout = 5;
    c.send_wait_timeout = 5;
    if (httpd_start(&server, &c) != ESP_OK) {
        ESP_LOGE(TAG, "Local HTTP start failed");
        return;
    }
    const httpd_uri_t routes[] = {
        {.uri = "/", .method = HTTP_GET, .handler = home},
        {.uri = "/api/status", .method = HTTP_GET, .handler = status},
        {.uri = "/api/configure", .method = HTTP_POST, .handler = configure},
        {.uri = "/api/km", .method = HTTP_POST, .handler = km},
        {.uri = "/api/request", .method = HTTP_POST, .handler = request_action}};
    for (size_t i = 0; i < sizeof routes / sizeof routes[0]; i++)
        httpd_register_uri_handler(server, &routes[i]);
}
static void wifi_event(void *arg, esp_event_base_t base, int32_t id, void *data) {
    (void)arg;
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START)
        esp_wifi_connect();
    else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        xEventGroupClearBits(net_events, 1);
        if (retries++ >= 8)
            xEventGroupSetBits(net_events, 2);
        if (!provisioning)
            esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *e = data;
        snprintf(ip, sizeof ip, IPSTR, IP2STR(&e->ip_info.ip));
        retries = 0;
        xEventGroupSetBits(net_events, 1);
    }
}
static void show_setup(void) {
    uint8_t mac[6];
    esp_read_mac(mac, ESP_MAC_WIFI_SOFTAP);
    snprintf(ap_name, sizeof ap_name, "CLIKVM-%02X%02X", mac[4], mac[5]);
    wifi_config_t ap = {.ap = {.channel = 6, .max_connection = 2, .authmode = WIFI_AUTH_WPA2_PSK}};
    strcpy((char *)ap.ap.ssid, ap_name);
    ap.ap.ssid_len = strlen(ap_name);
    strcpy((char *)ap.ap.password, local_key);
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &ap));
    ESP_ERROR_CHECK(esp_wifi_start());
    provisioning = true;
    strcpy(ip, "192.168.4.1");
    clik_local_server_start();
    clik_board_status("SETUP WIFI", ap_name, local_key, "192.168.4.1");
}
esp_err_t clik_connect_or_provision(clik_settings_t *s) {
    ESP_RETURN_ON_ERROR(esp_netif_init(), TAG, "netif");
    ESP_RETURN_ON_ERROR(esp_event_loop_create_default(), TAG, "event loop");
    esp_netif_create_default_wifi_sta();
    esp_netif_create_default_wifi_ap();
    net_events = xEventGroupCreate();
    ESP_RETURN_ON_FALSE(net_events, ESP_ERR_NO_MEM, TAG, "network events");
    wifi_init_config_t c = WIFI_INIT_CONFIG_DEFAULT();
    ESP_RETURN_ON_ERROR(esp_wifi_init(&c), TAG, "WiFi init");
    ESP_RETURN_ON_ERROR(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event, NULL),
                        TAG, "WiFi events");
    ESP_RETURN_ON_ERROR(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event, NULL),
                        TAG, "IP events");
    uint8_t random[6];
    esp_fill_random(random, sizeof random);
    for (int i = 0; i < 6; i++)
        snprintf(local_key + i * 2, 3, "%02X", random[i]);
    esp_err_t loaded = clik_load_settings(s);
    if (loaded == ESP_OK) {
        wifi_config_t wc = {0};
        memcpy(wc.sta.ssid, s->ssid, strlen(s->ssid));
        memcpy(wc.sta.password, s->password, strlen(s->password));
        ESP_RETURN_ON_ERROR(esp_wifi_set_mode(WIFI_MODE_STA), TAG, "STA mode");
        ESP_RETURN_ON_ERROR(esp_wifi_set_config(WIFI_IF_STA, &wc), TAG, "WiFi config");
        ESP_RETURN_ON_ERROR(esp_wifi_start(), TAG, "WiFi start");
        clik_board_status("CONNECTING WIFI", s->ssid, "USB HOST OFF", "");
        EventBits_t bits =
            xEventGroupWaitBits(net_events, 3, pdFALSE, pdFALSE, pdMS_TO_TICKS(30000));
        if (bits & 1) {
            provisioning = false;
            clik_local_server_start();
            esp_sntp_setoperatingmode(SNTP_OPMODE_POLL);
            esp_sntp_setservername(0, "pool.ntp.org");
            esp_sntp_init();
            return ESP_OK;
        }
        provisioning = true;
        esp_wifi_stop();
    }
    show_setup();
    return ESP_ERR_NOT_FOUND;
}
void clik_app_request_setup(void) {
    /* Local physical long hold requests WiFi/server setup next boot. Preserve
     * unique ADB host key in its separate NVS namespace. */
    nvs_handle_t h;
    if (nvs_open("clik_config", NVS_READWRITE, &h) == ESP_OK) {
        nvs_erase_all(h);
        nvs_commit(h);
        nvs_close(h);
    }
    esp_restart();
}
