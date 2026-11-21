/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"
typedef struct {
    bool pressed;
    int x;
    int y;
} clik_board_input_t;
esp_err_t clik_board_init(void);
const char *clik_board_name(void);
/* Host power starts OFF. Caller must receive physical Start after connecting
 * the documented power/data adapter; a PC must not remain on the data port. */
esp_err_t clik_board_host_power(bool enable);
void clik_board_status(const char *title, const char *line1, const char *line2, const char *line3);
clik_board_input_t clik_board_input(void);
esp_err_t clik_board_audio_init(void);
/* PCM16 mono 16kHz; underlying ES7210 captures two microphone channels. */
esp_err_t clik_board_record(int16_t *samples, size_t count);
esp_err_t clik_board_play(const int16_t *samples, size_t count);
