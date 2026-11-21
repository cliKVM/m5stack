/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include "esp_err.h"
#include "clik_protocol.h"
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
typedef struct {
    char base_url[256], device_id[64], device_token[192];
    bool audio_available;
} clik_cloud_config_t;
typedef struct {
    bool present, capture_requested, screenshot, wait;
    char action_id[65], lease_id[65], state[48];
    uint32_t based_on_frame, valid_for_ms, wait_ms;
    int64_t received_at_us;
    clik_action_t command;
} clik_cloud_action_t;
esp_err_t clik_cloud_init(const clik_cloud_config_t *config);
bool clik_cloud_url_allowed(const char *url);
esp_err_t clik_cloud_start_session(uint32_t usb_generation);
void clik_cloud_clear_session(void);
const char *clik_cloud_session(void);
uint32_t clik_cloud_frame(void);
unsigned clik_cloud_http_status(void);
bool clik_cloud_session_expired(void);
esp_err_t clik_cloud_capture(void);
esp_err_t clik_cloud_intent(const char *text);
esp_err_t clik_cloud_next(clik_cloud_action_t *out);
esp_err_t clik_cloud_result(const clik_cloud_action_t *action, const char *status,
                            const char *detail);
esp_err_t clik_cloud_stt(const uint8_t *wav, size_t length, char *text, size_t capacity);
esp_err_t clik_cloud_speak(const char *text);
/* One caller/task owns all cloud operations. Device token is never logged. */
