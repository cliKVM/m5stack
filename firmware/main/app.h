/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include "clik_cloud.h"
#include "esp_err.h"
#include <stdbool.h>
typedef struct {
    char ssid[33], password[65];
    clik_cloud_config_t cloud;
} clik_settings_t;
typedef enum { USER_CAPTURE, USER_INTENT, USER_VOICE, USER_PAUSE, USER_RESUME } user_request_kind_t;
typedef struct {
    user_request_kind_t kind;
    char text[1025];
} user_request_t;
esp_err_t clik_load_settings(clik_settings_t *settings);
esp_err_t clik_connect_or_provision(clik_settings_t *settings);
void clik_local_server_start(void);
bool clik_wifi_ready(void);
const char *clik_ip(void);
const char *clik_control_key(void);
bool clik_app_enqueue(const user_request_t *request);
bool clik_app_host_started(void);
bool clik_app_paused(void);
void clik_app_request_setup(void);
