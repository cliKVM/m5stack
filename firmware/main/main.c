/* SPDX-License-Identifier: Apache-2.0 */
#include "app.h"
#include "clik_usb.h"
#include "clik_board.h"
#include "clik_hid.h"
#include "sdkconfig.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "nvs_flash.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"

static const char *TAG = "clik_app";
static QueueHandle_t user_queue;
static volatile bool host_started, paused;
static bool app_initialized;
static bool audio_ready;
#if CONFIG_CLIK_MODE_ANDROID_HOST
static uint32_t session_generation;
static bool pending_result, need_frame;
static clik_cloud_action_t pending_action;
static char pending_status[16], pending_detail[96];
#else
static clik_controller_target_t remote_target;
#endif
#if CONFIG_CLIK_TTS_ENABLED
static bool completion_spoken;
#endif
bool clik_app_host_started(void) {
    return host_started;
}
bool clik_app_paused(void) {
    return paused;
}
const char *clik_app_mode(void) {
#if CONFIG_CLIK_MODE_ANDROID_HOST
    return "android_usb_host";
#elif CONFIG_CLIK_MODE_USB_HID
    return "usb_hid_device";
#else
    return "network_controller";
#endif
}
bool clik_app_input_ready(void) {
    if (paused)
        return false;
#if CONFIG_CLIK_MODE_ANDROID_HOST
    clik_usb_status_t s;
    clik_usb_get_status(&s);
    return host_started && s.aoa_ready;
#elif CONFIG_CLIK_MODE_USB_HID
    return host_started && clik_hid_ready();
#else
    return false;
#endif
}
esp_err_t clik_app_send_key(uint8_t modifiers, uint8_t usage) {
    if (!clik_app_input_ready())
        return ESP_ERR_INVALID_STATE;
#if CONFIG_CLIK_MODE_ANDROID_HOST
    return clik_usb_send_key(modifiers, usage, 3000);
#elif CONFIG_CLIK_MODE_USB_HID
    return clik_hid_key(modifiers, usage, 3000);
#else
    return ESP_ERR_NOT_SUPPORTED;
#endif
}
esp_err_t clik_app_send_mouse(uint8_t buttons, int8_t x, int8_t y, int8_t wheel) {
    if (!clik_app_input_ready())
        return ESP_ERR_INVALID_STATE;
#if CONFIG_CLIK_MODE_ANDROID_HOST
    return clik_usb_send_mouse(buttons, x, y, wheel, 3000);
#elif CONFIG_CLIK_MODE_USB_HID
    return clik_hid_mouse(buttons, x, y, wheel, 3000);
#else
    return ESP_ERR_NOT_SUPPORTED;
#endif
}
bool clik_app_enqueue(const user_request_t *r) {
    if (r->kind == USER_PAUSE)
        paused = true;
    if (!user_queue || xQueueSend(user_queue, r, 0) != pdTRUE) {
        ESP_LOGW(TAG, "User queue full");
        return false;
    }
    return true;
}
static void show(const char *title, const char *detail) {
    clik_board_status(title, detail, clik_ip(), clik_control_key());
}
static void store32(uint8_t *p, uint32_t n) {
    p[0] = n;
    p[1] = n >> 8;
    p[2] = n >> 16;
    p[3] = n >> 24;
}
static void wav_header(uint8_t *p, uint32_t samples) {
    memset(p, 0, 44);
    memcpy(p, "RIFF", 4);
    store32(p + 4, 36 + samples * 2);
    memcpy(p + 8, "WAVEfmt ", 8);
    store32(p + 16, 16);
    p[20] = 1;
    p[22] = 1;
    store32(p + 24, 16000);
    store32(p + 28, 32000);
    p[32] = 2;
    p[34] = 16;
    memcpy(p + 36, "data", 4);
    store32(p + 40, samples * 2);
}
static void voice(void) {
    if (!audio_ready) {
        show("AUDIO UNAVAILABLE", "SEE UART LOG");
        return;
    }
#if CONFIG_CLIK_MODE_ANDROID_HOST
    if (!clik_cloud_session()[0]) {
        show("ADB REQUIRED", "FINISH PHONE SETUP");
        return;
    }
#else
    if (!remote_target.ready) {
        show("TARGET UNAVAILABLE", "CHECK GATEWAY PANEL");
        return;
    }
#endif
    size_t count = CONFIG_CLIK_AUDIO_SECONDS * 16000;
    uint8_t *wav = heap_caps_malloc(44 + count * 2, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!wav) {
        show("NO AUDIO BUFFER", "");
        return;
    }
    wav_header(wav, count);
    show("RECORDING", "SPEAK YOUR TASK");
    esp_err_t e = clik_board_record((int16_t *)(wav + 44), count);
    char *text = calloc(1, 4097);
    if (e == ESP_OK && text) {
        show("TRANSCRIBING", "");
        e = clik_cloud_stt(wav, 44 + count * 2, text, 4097);
    } else if (!text)
        e = ESP_ERR_NO_MEM;
    memset(wav, 0, 44 + count * 2);
    free(wav);
#if CONFIG_CLIK_MODE_ANDROID_HOST
    if (e == ESP_OK)
        e = clik_cloud_capture();
#endif
    if (e == ESP_OK && paused)
        e = ESP_ERR_INVALID_STATE;
    if (e == ESP_OK)
        e = clik_cloud_intent(text);
#if CONFIG_CLIK_TTS_ENABLED
    if (e == ESP_OK)
        completion_spoken = false;
#endif
    if (text) {
        memset(text, 0, 4097);
        free(text);
    }
    show(e == ESP_OK ? "TASK SUBMITTED" : "VOICE ERROR",
         e == ESP_OK ? "CHECK GATEWAY PANEL" : esp_err_to_name(e));
}
static void local_inputs(void *unused) {
    (void)unused;
    bool last = false, long_sent = false;
    int64_t started = 0;
#if CONFIG_CLIK_BOARD_CORES3 && CONFIG_CLIK_MODE_ANDROID_HOST
    clik_board_input_t first = {0};
#endif
    for (;;) {
        clik_board_input_t in = clik_board_input();
        int64_t now = esp_timer_get_time();
        if (in.pressed && !last) {
            started = now;
#if CONFIG_CLIK_BOARD_CORES3 && CONFIG_CLIK_MODE_ANDROID_HOST
            first = in;
#endif
            long_sent = false;
        }
        if (in.pressed && !long_sent && now - started > 7000000) {
            long_sent = true;
            clik_app_request_setup();
        }
        if (!in.pressed && last && !long_sent && now - started > 50000) {
            if (!app_initialized) {
                last = in.pressed;
                vTaskDelay(pdMS_TO_TICKS(30));
                continue;
            }
#if CONFIG_CLIK_MODE_ANDROID_HOST
            if (!host_started) {
                show("STARTING HOST", "CONNECT PHONE USB");
                esp_err_t e = clik_board_host_power(true);
                if (e == ESP_OK) {
                    clik_usb_config_t u = {.max_capture_bytes =
                                               CONFIG_CLIK_CAPTURE_MAX_MIB * 1024u * 1024u};
                    e = clik_usb_start(&u);
                }
                if (e == ESP_OK) {
                    host_started = true;
                    show("USB HOST READY", "UNLOCK PHONE");
                } else {
                    clik_board_host_power(false);
                    show("USB HOST ERROR", esp_err_to_name(e));
                }
            } else {
                clik_usb_status_t us;
                clik_usb_get_status(&us);
                if (!us.adb_ready && us.aoa_ready) {
                    /* CoreS3 touch: top-left Tab, top-right Enter, bottom-left
                     * Escape, bottom-right click. Waveshare BOOT sends Tab. */
#if CONFIG_CLIK_BOARD_CORES3
                    if (first.y >= 120 && first.x >= 160) {
                        clik_usb_send_mouse(1, 0, 0, 0, 3000);
                        clik_usb_send_mouse(0, 0, 0, 0, 3000);
                    } else
                        clik_usb_send_key(0, first.y >= 120 ? 41 : first.x >= 160 ? 40 : 43, 3000);
#else
                    clik_usb_send_key(0, 43, 3000);
#endif
                } else if (us.adb_ready) {
                    user_request_t r = {.kind = USER_VOICE};
                    clik_app_enqueue(&r);
                }
            }
#elif CONFIG_CLIK_MODE_USB_HID
            if (!host_started) {
                /* Device role: VBUS output remains off. The PC supplies the bus. */
                esp_err_t e = clik_board_host_power(false);
                if (e == ESP_OK)
                    e = clik_hid_start();
                if (e == ESP_OK) {
                    host_started = true;
                    show("HID DEVICE STARTED", "CONNECT PC USB");
                } else
                    show("HID START ERROR", esp_err_to_name(e));
            } else {
                user_request_t r = {.kind = USER_VOICE};
                clik_app_enqueue(&r);
            }
#else
            user_request_t r = {.kind = USER_VOICE};
            clik_app_enqueue(&r);
#endif
        }
        last = in.pressed;
        vTaskDelay(pdMS_TO_TICKS(30));
    }
}
#if CONFIG_CLIK_MODE_ANDROID_HOST
static esp_err_t execute(const clik_cloud_action_t *a, bool *attempted) {
    *attempted = false;
    if (paused)
        return ESP_ERR_INVALID_STATE;
    if (a->based_on_frame != clik_cloud_frame())
        return ESP_ERR_INVALID_STATE;
    int64_t remaining =
        (int64_t)a->valid_for_ms - (esp_timer_get_time() - a->received_at_us) / 1000;
    if (remaining <= 0)
        return ESP_ERR_TIMEOUT;
    clik_usb_status_t us;
    clik_usb_get_status(&us);
    if (!us.adb_ready || us.connection_generation != session_generation)
        return ESP_ERR_INVALID_STATE;
    if (a->screenshot) {
        *attempted = true;
        return clik_cloud_capture();
    }
    if (a->wait) {
        if (a->wait_ms > (uint32_t)remaining)
            return ESP_ERR_TIMEOUT;
        *attempted = true;
        vTaskDelay(pdMS_TO_TICKS(a->wait_ms));
        return ESP_OK;
    }
    *attempted = true;
    return clik_usb_execute(&a->command, remaining < 10000 ? (uint32_t)remaining : 10000);
}
static void reset_cloud_session(void) {
    clik_cloud_clear_session();
    session_generation = 0;
    pending_result = false;
    need_frame = false;
#if CONFIG_CLIK_TTS_ENABLED
    completion_spoken = false;
#endif
}
#else
static void remote_loop(void) {
    int64_t next_poll = 0;
    char shown_state[48] = "", shown_session[65] = "";
#if CONFIG_CLIK_MODE_USB_HID
    bool shown_usb_started = false, shown_hid_ready = false;
#endif
#if CONFIG_CLIK_MODE_USB_HID
    show("TAP TO ENABLE HID", "PC BUS POWER ONLY");
#else
    show("NETWORK CONTROLLER", "LOOKING FOR TARGET");
#endif
    for (;;) {
        user_request_t q;
        if (xQueueReceive(user_queue, &q, pdMS_TO_TICKS(50)) == pdTRUE) {
            esp_err_t e = ESP_OK;
            if (q.kind == USER_PAUSE) {
                paused = true;
                e = clik_cloud_controller_cancel();
                show(e == ESP_OK ? "STOP REQUESTED" : "STOP NOT CONFIRMED",
                     e == ESP_OK ? "IN-FLIGHT MAY FINISH" : "CHECK GATEWAY PANEL");
            } else if (q.kind == USER_RESUME) {
                paused = false;
                show("NEW TASKS ENABLED", "OLD GOAL NOT RESUMED");
            } else if (!paused && clik_wifi_ready()) {
                /* Refresh binding before every command: do not send a remembered
                 * session or select a target based on a stale local cache. */
                e = clik_cloud_controller_target(&remote_target);
                if (e != ESP_OK || !remote_target.ready)
                    show("TARGET UNAVAILABLE", "CHECK GATEWAY PANEL");
                else if (q.kind == USER_VOICE)
                    voice();
                else if (q.kind == USER_CAPTURE) {
                    e = clik_cloud_capture();
                    show(e == ESP_OK ? "OBSERVATION REQUESTED" : "REQUEST ERROR",
                         e == ESP_OK ? remote_target.target_device_id : esp_err_to_name(e));
                } else if (q.kind == USER_INTENT) {
                    e = clik_cloud_intent(q.text);
#if CONFIG_CLIK_TTS_ENABLED
                    if (e == ESP_OK)
                        completion_spoken = false;
#endif
                    show(e == ESP_OK ? "TASK SUBMITTED" : "TASK ERROR",
                         e == ESP_OK ? remote_target.target_device_id : esp_err_to_name(e));
                }
            }
            memset(&q, 0, sizeof q);
        }
        if (!clik_wifi_ready() || esp_timer_get_time() < next_poll)
            continue;
        next_poll = esp_timer_get_time() + 2000000;
        esp_err_t e = clik_cloud_controller_target(&remote_target);
        if (e != ESP_OK) {
            show("GATEWAY UNAVAILABLE", esp_err_to_name(e));
            shown_state[0] = 0;
            continue;
        }
        bool changed = strcmp(shown_state, remote_target.state) ||
                       strcmp(shown_session, remote_target.session_id);
#if CONFIG_CLIK_MODE_USB_HID
        changed =
            changed || shown_usb_started != host_started || shown_hid_ready != clik_hid_ready();
        shown_usb_started = host_started;
        shown_hid_ready = clik_hid_ready();
#endif
        if (changed) {
            snprintf(shown_state, sizeof shown_state, "%s", remote_target.state);
            snprintf(shown_session, sizeof shown_session, "%s", remote_target.session_id);
            if (!paused) {
#if CONFIG_CLIK_MODE_USB_HID
                if (!host_started)
                    show("TAP TO ENABLE HID", remote_target.target_device_id);
                else
#endif
                    show(remote_target.ready ? remote_target.state : "TARGET UNAVAILABLE",
                         remote_target.target_device_id);
            }
        }
#if CONFIG_CLIK_TTS_ENABLED
        if (strcmp(remote_target.state, "done") != 0)
            completion_spoken = false;
        else if (!paused && !completion_spoken && audio_ready) {
            completion_spoken = true;
            clik_cloud_speak("Zadanie zostało wykonane.");
        }
#endif
    }
}
#endif
void app_main(void) {
    esp_err_t e = nvs_flash_init();
    /* Never erase NVS automatically: it contains the device's trusted ADB key. */
    if (e != ESP_OK) {
        ESP_LOGE(TAG, "NVS init failed: %s; manual recovery required", esp_err_to_name(e));
        return;
    }
    ESP_ERROR_CHECK(clik_board_init());
    user_queue = xQueueCreate(4, sizeof(user_request_t));
    if (!user_queue) {
        show("NO REQUEST QUEUE", "");
        return;
    }
    clik_settings_t settings = {0};
    e = clik_connect_or_provision(&settings);
    if (e != ESP_OK) {
        while (1)
            vTaskDelay(pdMS_TO_TICKS(1000));
    }
    audio_ready = clik_board_audio_init() == ESP_OK;
    settings.cloud.audio_available = audio_ready;
    xTaskCreate(local_inputs, "local_input", 6144, NULL, 4, NULL);
    e = clik_cloud_init(&settings.cloud);
    memset(&settings, 0, sizeof settings);
    if (e != ESP_OK) {
        show("INVALID CONFIG", "HOLD 7S FOR SETUP");
        return;
    }
    app_initialized = true;
#if !CONFIG_CLIK_MODE_ANDROID_HOST
    remote_loop();
#else
    show("TAP TO START USB", "PHONE + HOST POWER");
    clik_usb_state_t shown = CLIK_USB_DISCONNECTED;
    int64_t next_poll = 0;
    for (;;) {
        clik_usb_status_t us;
        clik_usb_get_status(&us);
        if (host_started && us.state != shown) {
            shown = us.state;
            show(clik_usb_state_name(us.state),
                 us.adb_ready ? "TAP TO SPEAK" : "OPEN LOCAL KM PANEL");
        }
        if (!us.adb_ready ||
            (session_generation && us.connection_generation != session_generation)) {
            if (session_generation)
                reset_cloud_session();
        }
        user_request_t q;
        if (xQueueReceive(user_queue, &q, pdMS_TO_TICKS(50)) == pdTRUE) {
            if (q.kind == USER_PAUSE) {
                paused = true;
                show("PAUSED", "");
            } else if (q.kind == USER_RESUME) {
                paused = false;
                need_frame = true;
                show("RESUMED", "");
            } else if (!paused && us.adb_ready && session_generation && !pending_result) {
                if (q.kind == USER_VOICE)
                    voice();
                else if (q.kind == USER_CAPTURE)
                    need_frame = true;
                else if (q.kind == USER_INTENT) {
                    e = clik_cloud_capture();
                    if (e == ESP_OK)
                        e = clik_cloud_intent(q.text);
                    show(e == ESP_OK ? "TASK SUBMITTED" : "TASK ERROR",
                         e == ESP_OK ? "CHECK GATEWAY PANEL" : esp_err_to_name(e));
                }
            }
            memset(&q, 0, sizeof q);
        }
        if (!host_started || !us.adb_ready || !clik_wifi_ready()) {
            vTaskDelay(pdMS_TO_TICKS(100));
            continue;
        }
        if (!session_generation) {
            e = clik_cloud_start_session(us.connection_generation);
            if (e != ESP_OK) {
                show("SERVER CONNECTION", esp_err_to_name(e));
                vTaskDelay(pdMS_TO_TICKS(3000));
                continue;
            }
            session_generation = us.connection_generation;
            need_frame = true;
        }
        /* A lost result response must never cause a second execution. Keep the
         * exact result locally and retry its idempotent POST before any capture. */
        if (pending_result) {
            e = clik_cloud_result(&pending_action, pending_status, pending_detail);
            if (e != ESP_OK && clik_cloud_session_expired()) {
                reset_cloud_session();
                show("NEW SESSION", "OLD TASK STOPPED");
                continue;
            }
            if (e != ESP_OK && clik_cloud_http_status() != 409) {
                show("RESULT PENDING", "NO ACTION REPLAY");
                vTaskDelay(pdMS_TO_TICKS(2000));
                continue;
            }
            if (e != ESP_OK)
                show("VERIFY RESULT", "OPEN GATEWAY PANEL");
            pending_result = false;
            need_frame = true;
        }
        /* Pausing blocks subsequent input, not acknowledgement of input that
         * already reached the phone. Otherwise a pause would expire the lease. */
        if (paused) {
            vTaskDelay(pdMS_TO_TICKS(100));
            continue;
        }
        if (need_frame) {
            e = clik_cloud_capture();
            if (e != ESP_OK && clik_cloud_session_expired()) {
                reset_cloud_session();
                continue;
            }
            if (e != ESP_OK) {
                show("CAPTURE ERROR", esp_err_to_name(e));
                vTaskDelay(pdMS_TO_TICKS(1500));
                continue;
            }
            need_frame = false;
            show("READY", "TAP TO SPEAK");
        }
        if (esp_timer_get_time() < next_poll)
            continue;
        next_poll = esp_timer_get_time() + 1200000;
        clik_cloud_action_t a;
        e = clik_cloud_next(&a);
        if (e != ESP_OK && clik_cloud_session_expired()) {
            reset_cloud_session();
            continue;
        }
        if (e != ESP_OK) {
            show("SERVER WAIT", esp_err_to_name(e));
            continue;
        }
        if (a.capture_requested) {
            need_frame = true;
            continue;
        }
        if (!a.present) {
#if CONFIG_CLIK_TTS_ENABLED
            /* Speak once after completion, never while an action lease awaits
             * its result. TTS HTTP and playback can take longer than a lease. */
            if (strcmp(a.state, "done") != 0)
                completion_spoken = false;
            else if (!completion_spoken && audio_ready) {
                completion_spoken = true;
                clik_cloud_speak("Zadanie zostało wykonane.");
            }
#endif
            continue;
        }
#if CONFIG_CLIK_TTS_ENABLED
        completion_spoken = false;
#endif
        bool attempted = false;
        show("EXECUTING", a.state);
        e = execute(&a, &attempted);
        pending_action = a;
        pending_result = true;
        /* Transport failure can happen after input reached Android. Report
         * uncertainty so the server requires reconciliation, never auto replay. */
        strcpy(pending_status, e == ESP_OK ? "succeeded" : attempted ? "uncertain" : "failed");
        snprintf(pending_detail, sizeof pending_detail, "%s", esp_err_to_name(e));
    }
#endif
}
