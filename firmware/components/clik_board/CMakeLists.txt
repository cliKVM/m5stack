idf_component_register(SRCS "clik_board.c" "clik_audio.c"
    INCLUDE_DIRS "include" PRIV_INCLUDE_DIRS "."
    REQUIRES esp_lcd esp_driver_i2c esp_driver_spi esp_driver_gpio esp_driver_i2s
             esp_timer esp_codec_dev esp_lcd_ili9341)

