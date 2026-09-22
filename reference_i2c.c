/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Main program body
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2026 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */
/* Includes ------------------------------------------------------------------*/
#include "main.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h> //atoi
#include <string.h>
#include <math.h>
#include <stdarg.h>
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */
typedef enum {
    LOAD_STATE_IDLE,
    LOAD_STATE_TX_BUSY,     // Sending command
    LOAD_STATE_WAIT_SENSOR, // Waiting 30ms for sensor to process
    LOAD_STATE_RX_BUSY      // Receiving data
} LoadSensorState_t;
/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */
#define MAX_CMD_LEN 32
#define MODBUS_POLY 0xA001
#define MODBUS_RX_BUFFER_SIZE 16
#define ENC_POLL_MS 50
#define KILL_SWITCH_Pin GPIO_PIN_7
#define KILL_SWITCH_Port GPIOA
#define CMD_READ_LOAD   0x01
#define I2C_TIMEOUT_MS  20


/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */
uint8_t slave_address = 0x17;

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/
DAC_HandleTypeDef hdac;

I2C_HandleTypeDef hi2c1;
I2C_HandleTypeDef hi2c2;

UART_HandleTypeDef huart5;
UART_HandleTypeDef huart1;
UART_HandleTypeDef huart2;
UART_HandleTypeDef huart3;
DMA_HandleTypeDef hdma_usart1_rx;

/* USER CODE BEGIN PV */
LoadSensorState_t load_state = LOAD_STATE_IDLE;
uint32_t load_timer_tick = 0;      // Controls how often we poll the sensor
uint32_t sensor_proc_start = 0;    // Tracks the 30ms wait time
uint8_t i2c_cmd_buffer = CMD_READ_LOAD;
uint8_t i2c_rx_buffer[4];
float g_latest_load_val = 0.0f;
bool g_load_updated = false;

/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
static void MX_GPIO_Init(void);
static void MX_DMA_Init(void);
static void MX_USART2_UART_Init(void);
static void MX_USART1_UART_Init(void);
static void MX_I2C1_Init(void);
static void MX_I2C2_Init(void);
static void MX_DAC_Init(void);
static void MX_UART5_Init(void);
static void MX_USART3_UART_Init(void);
/* USER CODE BEGIN PFP */

/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */
char uart2_rx_buf[MAX_CMD_LEN];
uint8_t uart2_byte = 0;
uint8_t rx_index = 0;

int16_t current_velocity = 1000;
int16_t current_acc = 50;
int16_t current_dec = 50;

volatile bool command_ready = false;
char cmd_process_buf[MAX_CMD_LEN];

/* Encoder polling variables */
uint8_t enc_request_frame[8];
uint8_t modbus_rx_buffer[MODBUS_RX_BUFFER_SIZE];
volatile int32_t g_encoder_position = 0;
volatile uint8_t g_new_data_ready = 0;
volatile uint8_t g_dma_needs_restart = 0;
static uint32_t enc_poll_tick = 0;
static bool kill_active = false;

void append_crc(uint8_t *frame, uint16_t len);
void modbus_send(uint8_t *frame, uint16_t len);

void process_command(char *cmd) {
  if (strncmp(cmd, "VEL:", 4) == 0) {
    int v = atoi(cmd + 4);
    if (v >= -3000 && v <= 3000)
      current_velocity = v;
  } else if (strncmp(cmd, "ACC:", 4) == 0) {
    int a = atoi(cmd + 4);
    if (a >= 0 && a <= 3000)
      current_acc = a;
  } else if (strncmp(cmd, "DEC:", 4) == 0) {
    int d = atoi(cmd + 4);
    if (d >= 0 && d <= 3000)
      current_dec = d;
  } else if (strcmp(cmd, "START") == 0) {
    uint8_t frame[8];
    uint8_t mode[] = {0x01, 0x06, 0x62, 0x00, 0x00, 0x02};
    uint8_t vel[] = {0x01,
                     0x06,
                     0x62,
                     0x03,
                     ((uint16_t)current_velocity >> 8),
                     (uint16_t)current_velocity & 0xFF};
    uint8_t acc[] = {
        0x01, 0x06, 0x62, 0x04, (current_acc >> 8), current_acc & 0xFF};
    uint8_t dec[] = {
        0x01, 0x06, 0x62, 0x05, (current_dec >> 8), current_dec & 0xFF};
    uint8_t start[] = {0x01, 0x06, 0x60, 0x02, 0x00, 0x10};

    memcpy(frame, mode, 6);
    append_crc(frame, 6);
    modbus_send(frame, 8);
    memcpy(frame, vel, 6);
    append_crc(frame, 6);
    modbus_send(frame, 8);
    memcpy(frame, acc, 6);
    append_crc(frame, 6);
    modbus_send(frame, 8);
    memcpy(frame, dec, 6);
    append_crc(frame, 6);
    modbus_send(frame, 8);
    memcpy(frame, start, 6);
    append_crc(frame, 6);
    modbus_send(frame, 8);

  } else if (strcmp(cmd, "STOP") == 0) {
    uint8_t frame[8];
    uint8_t stop[] = {0x01, 0x06, 0x60, 0x02, 0x00, 0x40};
    memcpy(frame, stop, 6);
    append_crc(frame, 6);
    modbus_send(frame, 8);
  }
}

void check_kill_switch(void) {
  if (HAL_GPIO_ReadPin(KILL_SWITCH_Port, KILL_SWITCH_Pin) == GPIO_PIN_RESET) {
    if (!kill_active) {
      kill_active = true;
      uint8_t frame[8];
      uint8_t stop[] = {0x01, 0x06, 0x60, 0x02, 0x00, 0x40};
      memcpy(frame, stop, 6);
      append_crc(frame, 6);
      modbus_send(frame, 8);
      HAL_UART_Transmit(&huart2, (uint8_t *)"KILL\r\n", 6, 10);
    }
  } else {
    kill_active = false;
  }
}
uint16_t modbus_crc(uint8_t *data, uint16_t length) {
  uint16_t crc = 0xFFFF;
  for (uint16_t i = 0; i < length; i++) {
    crc ^= data[i];
    for (uint8_t j = 0; j < 8; j++) {
      if (crc & 0x01)
        crc = (crc >> 1) ^ MODBUS_POLY;
      else
        crc >>= 1;
    }
  }
  return crc;
}

void append_crc(uint8_t *frame, uint16_t len) {
  uint16_t crc = modbus_crc(frame, len);
  frame[len] = crc & 0xFF;
  frame[len + 1] = (crc >> 8);
}

void modbus_send(uint8_t *frame, uint16_t len) {
  HAL_UART_Transmit(&huart1, frame, len, 100);
  HAL_Delay(5); /* Modbus inter-frame gap: allow slave to process + respond */
}

void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart) {
  if (huart->Instance == USART2) {
    if (uart2_byte == '\n' || uart2_byte == '\r') {
      uart2_rx_buf[rx_index] = '\0';
      memcpy(cmd_process_buf, uart2_rx_buf, rx_index + 1);
      command_ready = true;
      rx_index = 0;
    } else if (rx_index < MAX_CMD_LEN - 1) {
      uart2_rx_buf[rx_index++] = uart2_byte;
    }
    HAL_UART_Receive_IT(&huart2, &uart2_byte, 1);
  }
}

/* Encoder DMA RX callback ---------------------------------------------------*/
void HAL_UARTEx_RxEventCallback(UART_HandleTypeDef *huart, uint16_t Size) {
  if (huart->Instance == USART1) {
    if (Size == 9) {
      uint16_t rx_crc = (modbus_rx_buffer[8] << 8) | modbus_rx_buffer[7];
      uint16_t calc_crc = modbus_crc(modbus_rx_buffer, 7);
      if (rx_crc == calc_crc) {
        uint16_t high_word = (modbus_rx_buffer[3] << 8) | modbus_rx_buffer[4];
        uint16_t low_word = (modbus_rx_buffer[5] << 8) | modbus_rx_buffer[6];
        g_encoder_position = (int32_t)((high_word << 16) | low_word);
        g_new_data_ready = 1;
      }
    }
    /* Re-arm DMA listener and disable Half-Transfer interrupt */
    if (HAL_UARTEx_ReceiveToIdle_DMA(&huart1, modbus_rx_buffer,
                                     MODBUS_RX_BUFFER_SIZE) == HAL_OK) {
      __HAL_DMA_DISABLE_IT(&hdma_usart1_rx, DMA_IT_HT);
    } else {
      g_dma_needs_restart = 1;
    }
  }
}

void HAL_I2C_MasterTxCpltCallback(I2C_HandleTypeDef *hi2c) {
    if (hi2c->Instance == I2C2) {
        // Transmission done, start the 30ms timer
        sensor_proc_start = HAL_GetTick();
        load_state = LOAD_STATE_WAIT_SENSOR;
    }
}
static float float_from_le_bytes(const uint8_t b[4]) {
    union { uint8_t b[4]; float f; } u;
    u.b[0] = b[0];
    u.b[1] = b[1];
    u.b[2] = b[2];
    u.b[3] = b[3];
    return u.f;
}

// Called automatically when I2C Receive (Data) finishes
void HAL_I2C_MasterRxCpltCallback(I2C_HandleTypeDef *hi2c) {
    if (hi2c->Instance == I2C2) {
        // Data received, convert it
        g_latest_load_val = float_from_le_bytes(i2c_rx_buffer);
        g_load_updated = true;
        load_state = LOAD_STATE_IDLE; // Reset for next time
    }
}


/* Assemble float from little-endian 4 bytes */


/* High-level: Get Load Value from specific address */


static void uart_printf(const char *fmt, ...)
{
    char buf[220];
    va_list args;
    va_start(args, fmt);
    int len = vsnprintf(buf, sizeof(buf), fmt, args);
    va_end(args);

    if (len <= 0) return;
    if (len > (int)sizeof(buf)) len = sizeof(buf);

    HAL_UART_Transmit(&huart2, (uint8_t *)buf, (uint16_t)len, 10);
}


/* USER CODE END 0 */

/**
  * @brief  The application entry point.
  * @retval int
  */
int main(void)
{

  /* USER CODE BEGIN 1 */


  /* USER CODE END 1 */

  /* MCU Configuration--------------------------------------------------------*/

  /* Reset of all peripherals, Initializes the Flash interface and the Systick. */
  HAL_Init();

  /* USER CODE BEGIN Init */

  /* USER CODE END Init */

  /* Configure the system clock */
  SystemClock_Config();

  /* USER CODE BEGIN SysInit */

  /* USER CODE END SysInit */

  /* Initialize all configured peripherals */
  MX_GPIO_Init();
  MX_DMA_Init();
  MX_USART2_UART_Init();
  MX_USART1_UART_Init();
  MX_I2C1_Init();
  MX_I2C2_Init();
  MX_DAC_Init();
  MX_UART5_Init();
  MX_USART3_UART_Init();
  /* USER CODE BEGIN 2 */
  HAL_UART_Receive_IT(&huart2, &uart2_byte, 1);

   /* Build encoder request frame: read 2 regs at 0x602C */
   uint8_t base_frame[6] = {0x01, 0x03, 0x60, 0x2C, 0x00, 0x02};
   uint16_t crc = modbus_crc(base_frame, 6);
   memcpy(enc_request_frame, base_frame, 6);
   enc_request_frame[6] = (crc & 0xFF);
   enc_request_frame[7] = (crc >> 8);

   /* Start DMA listening on USART1 */
   HAL_UARTEx_ReceiveToIdle_DMA(&huart1, modbus_rx_buffer,
                                MODBUS_RX_BUFFER_SIZE);
   __HAL_DMA_DISABLE_IT(&hdma_usart1_rx, DMA_IT_HT);
  /* USER CODE END 2 */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  while (1)
  {
    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */
	  if (command_ready) {
	        process_command(cmd_process_buf);
	        command_ready = false;
	      }

	      /* Kill switch poll — immediate motor stop */
	      check_kill_switch();

	      /* Non-blocking encoder poll at 20 Hz */
	      uint32_t now = HAL_GetTick();
	      if (now - enc_poll_tick >= ENC_POLL_MS) {
	        enc_poll_tick = now;
	        HAL_UART_Transmit(&huart1, enc_request_frame, 8, 10);
	      }

	      /* Transmit encoder position when new data arrives */
	      if (g_new_data_ready) {
	        g_new_data_ready = 0;
	        char enc_msg[24];
	        int len =
	            snprintf(enc_msg, sizeof(enc_msg), "E:%ld\r\n", g_encoder_position);
	        HAL_UART_Transmit(&huart2, (uint8_t *)enc_msg, len, 10);
	      }

	      switch (load_state) {
	              case LOAD_STATE_IDLE:
	                  // Poll sensor every 100ms (adjust as needed)
	                  if (now - load_timer_tick > 100) {
	                      load_timer_tick = now;

	                      // Start Non-blocking Transmit
	                      // (Address << 1) handles the HAL 7-bit shift requirement
	                      HAL_I2C_Master_Transmit_IT(&hi2c2, (slave_address << 1), &i2c_cmd_buffer, 1);
	                      load_state = LOAD_STATE_TX_BUSY;
	                  }
	                  break;

	              case LOAD_STATE_WAIT_SENSOR:
	                  // Non-blocking wait for 30ms
	                  if (now - sensor_proc_start >= 30) {
	                      // Time is up, request data (Non-blocking)
	                      HAL_I2C_Master_Receive_IT(&hi2c2, (slave_address << 1), i2c_rx_buffer, 4);
	                      load_state = LOAD_STATE_RX_BUSY;
	                  }
	                  break;

	              case LOAD_STATE_TX_BUSY:
	              case LOAD_STATE_RX_BUSY:
	                  // Do nothing, let the Interrupts (IT) handle the transition
	                  break;
	          }

	          /* --- Print Result (Only when new data arrives) --- */
	          if (g_load_updated) {
	              g_load_updated = false;
	              // Uses standard format. Requires "Float with printf" enabled (see below)
	              uart_printf("Sensor [0x%02X]: %.3f kg\r\n", slave_address, g_latest_load_val);
	          }


	      /* DMA self-recovery: retry if ISR re-arm failed */
	      if (g_dma_needs_restart) {
	        if (HAL_UARTEx_ReceiveToIdle_DMA(&huart1, modbus_rx_buffer,
	                                         MODBUS_RX_BUFFER_SIZE) == HAL_OK) {
	          __HAL_DMA_DISABLE_IT(&hdma_usart1_rx, DMA_IT_HT);
	          g_dma_needs_restart = 0;
	        }
	      }

  }
  /* USER CODE END 3 */
}

/**
  * @brief System Clock Configuration
  * @retval None
  */
void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

  /** Configure the main internal regulator output voltage
  */
  __HAL_RCC_PWR_CLK_ENABLE();
  __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE3);

  /** Initializes the RCC Oscillators according to the specified parameters
  * in the RCC_OscInitTypeDef structure.
  */
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSI;
  RCC_OscInitStruct.HSIState = RCC_HSI_ON;
  RCC_OscInitStruct.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSI;
  RCC_OscInitStruct.PLL.PLLM = 16;
  RCC_OscInitStruct.PLL.PLLN = 336;
  RCC_OscInitStruct.PLL.PLLP = RCC_PLLP_DIV4;
  RCC_OscInitStruct.PLL.PLLQ = 2;
  RCC_OscInitStruct.PLL.PLLR = 2;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    Error_Handler();
  }

  /** Initializes the CPU, AHB and APB buses clocks
  */
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV2;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_2) != HAL_OK)
  {
    Error_Handler();
  }
}

/**
  * @brief DAC Initialization Function
  * @param None
  * @retval None
  */
static void MX_DAC_Init(void)
{

  /* USER CODE BEGIN DAC_Init 0 */

  /* USER CODE END DAC_Init 0 */

  DAC_ChannelConfTypeDef sConfig = {0};

  /* USER CODE BEGIN DAC_Init 1 */

  /* USER CODE END DAC_Init 1 */

  /** DAC Initialization
  */
  hdac.Instance = DAC;
  if (HAL_DAC_Init(&hdac) != HAL_OK)
  {
    Error_Handler();
  }

  /** DAC channel OUT1 config
  */
  sConfig.DAC_Trigger = DAC_TRIGGER_NONE;
  sConfig.DAC_OutputBuffer = DAC_OUTPUTBUFFER_ENABLE;
  if (HAL_DAC_ConfigChannel(&hdac, &sConfig, DAC_CHANNEL_1) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN DAC_Init 2 */

  /* USER CODE END DAC_Init 2 */

}

/**
  * @brief I2C1 Initialization Function
  * @param None
  * @retval None
  */
static void MX_I2C1_Init(void)
{

  /* USER CODE BEGIN I2C1_Init 0 */

  /* USER CODE END I2C1_Init 0 */

  /* USER CODE BEGIN I2C1_Init 1 */

  /* USER CODE END I2C1_Init 1 */
  hi2c1.Instance = I2C1;
  hi2c1.Init.ClockSpeed = 100000;
  hi2c1.Init.DutyCycle = I2C_DUTYCYCLE_2;
  hi2c1.Init.OwnAddress1 = 0;
  hi2c1.Init.AddressingMode = I2C_ADDRESSINGMODE_7BIT;
  hi2c1.Init.DualAddressMode = I2C_DUALADDRESS_DISABLE;
  hi2c1.Init.OwnAddress2 = 0;
  hi2c1.Init.GeneralCallMode = I2C_GENERALCALL_DISABLE;
  hi2c1.Init.NoStretchMode = I2C_NOSTRETCH_DISABLE;
  if (HAL_I2C_Init(&hi2c1) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN I2C1_Init 2 */

  /* USER CODE END I2C1_Init 2 */

}

/**
  * @brief I2C2 Initialization Function
  * @param None
  * @retval None
  */
static void MX_I2C2_Init(void)
{

  /* USER CODE BEGIN I2C2_Init 0 */

  /* USER CODE END I2C2_Init 0 */

  /* USER CODE BEGIN I2C2_Init 1 */

  /* USER CODE END I2C2_Init 1 */
  hi2c2.Instance = I2C2;
  hi2c2.Init.ClockSpeed = 100000;
  hi2c2.Init.DutyCycle = I2C_DUTYCYCLE_2;
  hi2c2.Init.OwnAddress1 = 0;
  hi2c2.Init.AddressingMode = I2C_ADDRESSINGMODE_7BIT;
  hi2c2.Init.DualAddressMode = I2C_DUALADDRESS_DISABLE;
  hi2c2.Init.OwnAddress2 = 0;
  hi2c2.Init.GeneralCallMode = I2C_GENERALCALL_DISABLE;
  hi2c2.Init.NoStretchMode = I2C_NOSTRETCH_DISABLE;
  if (HAL_I2C_Init(&hi2c2) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN I2C2_Init 2 */

  /* USER CODE END I2C2_Init 2 */

}

/**
  * @brief UART5 Initialization Function
  * @param None
  * @retval None
  */
static void MX_UART5_Init(void)
{

  /* USER CODE BEGIN UART5_Init 0 */

  /* USER CODE END UART5_Init 0 */

  /* USER CODE BEGIN UART5_Init 1 */

  /* USER CODE END UART5_Init 1 */
  huart5.Instance = UART5;
  huart5.Init.BaudRate = 115200;
  huart5.Init.WordLength = UART_WORDLENGTH_8B;
  huart5.Init.StopBits = UART_STOPBITS_1;
  huart5.Init.Parity = UART_PARITY_NONE;
  huart5.Init.Mode = UART_MODE_TX_RX;
  huart5.Init.HwFlowCtl = UART_HWCONTROL_NONE;
  huart5.Init.OverSampling = UART_OVERSAMPLING_16;
  if (HAL_UART_Init(&huart5) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN UART5_Init 2 */

  /* USER CODE END UART5_Init 2 */

}

/**
  * @brief USART1 Initialization Function
  * @param None
  * @retval None
  */
static void MX_USART1_UART_Init(void)
{

  /* USER CODE BEGIN USART1_Init 0 */

  /* USER CODE END USART1_Init 0 */

  /* USER CODE BEGIN USART1_Init 1 */

  /* USER CODE END USART1_Init 1 */
  huart1.Instance = USART1;
  huart1.Init.BaudRate = 115200;
  huart1.Init.WordLength = UART_WORDLENGTH_8B;
  huart1.Init.StopBits = UART_STOPBITS_1;
  huart1.Init.Parity = UART_PARITY_NONE;
  huart1.Init.Mode = UART_MODE_TX_RX;
  huart1.Init.HwFlowCtl = UART_HWCONTROL_NONE;
  huart1.Init.OverSampling = UART_OVERSAMPLING_16;
  if (HAL_UART_Init(&huart1) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN USART1_Init 2 */

  /* USER CODE END USART1_Init 2 */

}

/**
  * @brief USART2 Initialization Function
  * @param None
  * @retval None
  */
static void MX_USART2_UART_Init(void)
{

  /* USER CODE BEGIN USART2_Init 0 */

  /* USER CODE END USART2_Init 0 */

  /* USER CODE BEGIN USART2_Init 1 */

  /* USER CODE END USART2_Init 1 */
  huart2.Instance = USART2;
  huart2.Init.BaudRate = 115200;
  huart2.Init.WordLength = UART_WORDLENGTH_8B;
  huart2.Init.StopBits = UART_STOPBITS_1;
  huart2.Init.Parity = UART_PARITY_NONE;
  huart2.Init.Mode = UART_MODE_TX_RX;
  huart2.Init.HwFlowCtl = UART_HWCONTROL_NONE;
  huart2.Init.OverSampling = UART_OVERSAMPLING_16;
  if (HAL_UART_Init(&huart2) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN USART2_Init 2 */

  /* USER CODE END USART2_Init 2 */

}

/**
  * @brief USART3 Initialization Function
  * @param None
  * @retval None
  */
static void MX_USART3_UART_Init(void)
{

  /* USER CODE BEGIN USART3_Init 0 */

  /* USER CODE END USART3_Init 0 */

  /* USER CODE BEGIN USART3_Init 1 */

  /* USER CODE END USART3_Init 1 */
  huart3.Instance = USART3;
  huart3.Init.BaudRate = 115200;
  huart3.Init.WordLength = UART_WORDLENGTH_8B;
  huart3.Init.StopBits = UART_STOPBITS_1;
  huart3.Init.Parity = UART_PARITY_NONE;
  huart3.Init.Mode = UART_MODE_TX_RX;
  huart3.Init.HwFlowCtl = UART_HWCONTROL_NONE;
  huart3.Init.OverSampling = UART_OVERSAMPLING_16;
  if (HAL_UART_Init(&huart3) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN USART3_Init 2 */

  /* USER CODE END USART3_Init 2 */

}

/**
  * Enable DMA controller clock
  */
static void MX_DMA_Init(void)
{

  /* DMA controller clock enable */
  __HAL_RCC_DMA2_CLK_ENABLE();

  /* DMA interrupt init */
  /* DMA2_Stream2_IRQn interrupt configuration */
  HAL_NVIC_SetPriority(DMA2_Stream2_IRQn, 0, 0);
  HAL_NVIC_EnableIRQ(DMA2_Stream2_IRQn);

}

/**
  * @brief GPIO Initialization Function
  * @param None
  * @retval None
  */
static void MX_GPIO_Init(void)
{
  GPIO_InitTypeDef GPIO_InitStruct = {0};
  /* USER CODE BEGIN MX_GPIO_Init_1 */

  /* USER CODE END MX_GPIO_Init_1 */

  /* GPIO Ports Clock Enable */
  __HAL_RCC_GPIOC_CLK_ENABLE();
  __HAL_RCC_GPIOH_CLK_ENABLE();
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOB_CLK_ENABLE();
  __HAL_RCC_GPIOD_CLK_ENABLE();

  /*Configure GPIO pin Output Level */
  HAL_GPIO_WritePin(LD2_GPIO_Port, LD2_Pin, GPIO_PIN_RESET);

  /*Configure GPIO pin Output Level */
  HAL_GPIO_WritePin(GPIOA, GPIO_PIN_7, GPIO_PIN_SET);

  /*Configure GPIO pin Output Level */
  HAL_GPIO_WritePin(GPIOB, GPIO_PIN_4, GPIO_PIN_RESET);

  /*Configure GPIO pin : B1_Pin */
  GPIO_InitStruct.Pin = B1_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_IT_FALLING;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  HAL_GPIO_Init(B1_GPIO_Port, &GPIO_InitStruct);

  /*Configure GPIO pin : LD2_Pin */
  GPIO_InitStruct.Pin = LD2_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(LD2_GPIO_Port, &GPIO_InitStruct);

  /*Configure GPIO pin : PA7 */
  GPIO_InitStruct.Pin = GPIO_PIN_7;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_PULLUP;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

  /*Configure GPIO pin : PB4 */
  GPIO_InitStruct.Pin = GPIO_PIN_4;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);

  /* USER CODE BEGIN MX_GPIO_Init_2 */

  /* USER CODE END MX_GPIO_Init_2 */
}

/* USER CODE BEGIN 4 */

/* USER CODE END 4 */

/**
  * @brief  This function is executed in case of error occurrence.
  * @retval None
  */
void Error_Handler(void)
{
  /* USER CODE BEGIN Error_Handler_Debug */
  /* User can add his own implementation to report the HAL error return state */
  __disable_irq();
  while (1)
  {
  }
  /* USER CODE END Error_Handler_Debug */
}
#ifdef USE_FULL_ASSERT
/**
  * @brief  Reports the name of the source file and the source line number
  *         where the assert_param error has occurred.
  * @param  file: pointer to the source file name
  * @param  line: assert_param error line source number
  * @retval None
  */
void assert_failed(uint8_t *file, uint32_t line)
{
  /* USER CODE BEGIN 6 */
  /* User can add his own implementation to report the file name and line number,
     ex: printf("Wrong parameters value: file %s on line %d\r\n", file, line) */
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */
