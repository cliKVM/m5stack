idf_component_register(SRCS "clik_usb.c" "clik_adb_auth.c"
                       INCLUDE_DIRS "include" PRIV_INCLUDE_DIRS "."
                       REQUIRES clik_protocol esp_timer
                       PRIV_REQUIRES usb nvs_flash mbedtls esp_hw_support)
