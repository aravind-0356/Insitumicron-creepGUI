/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Creep Testing Machine Firmware
  *                   - USART1 (DMA2 Stream2): Leedshine motor Modbus RTU (8N2 115200)
  *                   - USART2 (IT):           PC host link — commands IN, telemetry OUT (8N1 115200)
  *                   - USART3 (IT):           Micro-Epsilon LVDT sensor (9-bit, Even, 256kbps)
  *                   - I2C1   (IT):           Load cell signal conditioner (addr 0x17, 100kHz)
  *                   - PB4:                   Kill switch input (ABSOLUTE HIGHEST PRIORITY)
  ******************************************************************************
  */
/* USER CODE END Header */
/* Includes ------------------------------------------------------------------*/
#include "main.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */

/* Load cell non-blocking state machine */
typedef enum {
    LC_IDLE,
    LC_TX_BUSY,
    LC_WAIT_SENSOR,
    LC_RX_BUSY
} LoadCellState_t;

/* Motor state */
typedef enum {
    MOTOR_IDLE,
    MOTOR_RUNNING
} MotorState_t;

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */

#define MAX_CMD_LEN             64
#define MODBUS_POLY             0xA001
#define ENC_RX_BUF_SIZE         16      /* 9-byte Modbus response + margin */
#define ENC_POLL_MS             50      /* Encoder poll interval (20 Hz) */

/* Kill switch — PB4, active LOW (user confirmed) */
#define KILL_SWITCH_Pin         GPIO_PIN_4
#define KILL_SWITCH_Port        GPIOB

/* Load cell I2C */
#define LC_SLAVE_ADDR           0x17    /* 7-bit address from reference_i2c.c */
#define LC_CMD_READ             0x01
#define LC_POLL_MS              100     /* 10 Hz load cell poll */
#define LC_PROC_WAIT_MS         30      /* Sensor processing time after command */

/* LVDT sensor */
#define LVDT_RX_BUF_SIZE        256
#define LVDT_POLL_MS            50      /* 20 Hz LVDT poll */
#define LVDT_RESP_TIMEOUT_MS    45      /* Response timeout */

/* Telemetry output interval */
#define TELEMETRY_MS            50      /* 20 Hz telemetry to PC */

/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */
/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/
I2C_HandleTypeDef hi2c1;
I2C_HandleTypeDef hi2c2;

UART_HandleTypeDef huart1;
UART_HandleTypeDef huart2;
UART_HandleTypeDef huart3;
DMA_HandleTypeDef hdma_usart1_rx;

/* USER CODE BEGIN PV */

/* ── MOTOR ── */
static int16_t    current_velocity  = 0;
static uint16_t   current_acc       = 50;
static uint16_t   current_dec       = 50;
static MotorState_t motor_state     = MOTOR_IDLE;

/* ── KILL SWITCH ── */
static bool kill_active = false;

/* ── UART2 (PC ↔ STM32) RX — FIFO Command Queue (8 slots) ── */
#define CMD_QUEUE_SIZE 8
static char    uart2_rx_buf[MAX_CMD_LEN];
static uint8_t uart2_rx_byte  = 0;
static uint8_t uart2_rx_index = 0;
static char    cmd_queue[CMD_QUEUE_SIZE][MAX_CMD_LEN];
static volatile uint8_t cmd_queue_head = 0;
static volatile uint8_t cmd_queue_tail = 0;

/* ── ENCODER (USART1 DMA — Leedshine motor encoder, homing only) ── */
static uint8_t  enc_request_frame[8];
static uint8_t  enc_rx_buffer[ENC_RX_BUF_SIZE];
volatile int32_t g_encoder_position = 0;
volatile uint8_t g_enc_data_ready   = 0;
volatile uint8_t g_enc_dma_restart  = 0;
volatile uint8_t encoder_bus_busy   = 0;
static uint32_t  last_enc_poll      = 0;

/* ── MICRO-EPSILON SENSOR (USART3 IT) ── */
static float scaled_val = 0.0f;
static float unscaled_val = 0.0f;
static float lvdt_tare_offset = -6.0f; // Tare baseline for fully relaxed position
static uint32_t last_me_req_tick = 0;
static uint8_t lvdt_rx_buf[256];
static uint16_t lvdt_rx_idx = 0;
static uint8_t lvdt_rx_byte;
static float g_displacement_mm = 0.0f;

/* ── LOAD CELL (I2C1 IT — signal conditioner addr 0x17) ── */
static LoadCellState_t lc_state      = LC_IDLE;
static uint32_t        lc_poll_tick  = 0;
static uint32_t        lc_proc_start = 0;
static uint8_t         lc_cmd_byte   = LC_CMD_READ;
static uint8_t         lc_rx_buf[4];
static float           g_load_kg     = 0.0f;  /* Raw value in kg from conditioner */
static volatile bool   g_load_updated = false;

/* ── TELEMETRY ── */
static uint32_t last_telemetry_tick = 0;

/* ── STALL DETECTION ── */
static uint32_t last_stall_check = 0;
static int32_t  last_stall_pos = 0;

/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
static void MX_GPIO_Init(void);
static void MX_DMA_Init(void);
static void MX_USART2_UART_Init(void);
static void MX_USART1_UART_Init(void);
static void MX_USART3_UART_Init(void);
static void MX_I2C1_Init(void);
static void MX_I2C2_Init(void);

/* USER CODE BEGIN PFP */
static void     Process_Commands(void);
static void     Check_Kill_Switch(void);
static void     Process_Encoder(void);
static void     Process_LVDT(void);
static void     Process_LoadCell(void);
static uint16_t modbus_crc(uint8_t *data, uint16_t length);
static void     append_crc(uint8_t *frame, uint16_t len);
static void     modbus_send_motor(uint8_t *frame, uint16_t len);
static void     Leadshine_Send_Start(int16_t vel);
static void     Leadshine_Send_Stop(void);
static void     Leadshine_Send_VelOnly(int16_t vel);
/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */

/* ============================================================
 * MODBUS CRC-16
 * ============================================================ */
static uint16_t modbus_crc(uint8_t *data, uint16_t length)
{
    uint16_t crc = 0xFFFF;
    for (uint16_t i = 0; i < length; i++) {
        crc ^= data[i];
        for (uint8_t j = 0; j < 8; j++) {
            if (crc & 0x01) crc = (crc >> 1) ^ MODBUS_POLY;
            else             crc >>= 1;
        }
    }
    return crc;
}

static void append_crc(uint8_t *frame, uint16_t len)
{
    uint16_t crc = modbus_crc(frame, len);
    frame[len]     = crc & 0xFF;
    frame[len + 1] = (crc >> 8);
}

/* ============================================================
 * MOTOR DRIVER — Leedshine via USART1 Modbus
 * Sets encoder_bus_busy to block encoder polling during TX.
 * ============================================================ */
static void modbus_send_motor(uint8_t *frame, uint16_t len)
{
    encoder_bus_busy = 1;
    HAL_UART_Transmit(&huart1, frame, len, 100);
    HAL_Delay(5);       /* Modbus inter-frame gap */
    encoder_bus_busy = 0;
}

static void Leadshine_Send_Start(int16_t vel)
{
    Check_Kill_Switch();
    if (kill_active) return;

    uint8_t frame[8];
    uint8_t mode[]  = {0x01, 0x06, 0x62, 0x00, 0x00, 0x02};
    uint8_t vel_f[] = {0x01, 0x06, 0x62, 0x03,
                       (uint8_t)((uint16_t)vel >> 8),
                       (uint8_t)((uint16_t)vel & 0xFF)};
    uint8_t acc[]   = {0x01, 0x06, 0x62, 0x04,
                       (uint8_t)(current_acc >> 8),
                       (uint8_t)(current_acc & 0xFF)};
    uint8_t dec[]   = {0x01, 0x06, 0x62, 0x05,
                       (uint8_t)(current_dec >> 8),
                       (uint8_t)(current_dec & 0xFF)};
    uint8_t start[] = {0x01, 0x06, 0x60, 0x02, 0x00, 0x10};

    uint8_t *cmds[] = {mode, vel_f, acc, dec, start};

    for (int i = 0; i < 5; i++) {
        Check_Kill_Switch();
        if (kill_active) return;

        memcpy(frame, cmds[i], 6);
        append_crc(frame, 6);
        modbus_send_motor(frame, 8);
    }
}

static void Leadshine_Send_Stop(void)
{
    uint8_t frame[8];
    uint8_t stop[] = {0x01, 0x06, 0x60, 0x02, 0x00, 0x40};
    memcpy(frame, stop, 6);
    append_crc(frame, 6);
    modbus_send_motor(frame, 8);
}

/* Live velocity update — only writes the velocity register, no mode/acc/dec resend */
static void Leadshine_Send_VelOnly(int16_t vel)
{
    Check_Kill_Switch();
    if (kill_active) return;

    uint8_t frame[8];
    uint8_t vel_f[] = {0x01, 0x06, 0x62, 0x03,
                       (uint8_t)((uint16_t)vel >> 8),
                       (uint8_t)((uint16_t)vel & 0xFF)};
    memcpy(frame, vel_f, 6);
    append_crc(frame, 6);
    modbus_send_motor(frame, 8);
}

/* ============================================================
 * KILL SWITCH — PB4, ABSOLUTE HIGHEST PRIORITY
 * Active LOW. Immediately stops motor and notifies PC.
 * ============================================================ */
static void Check_Kill_Switch(void)
{
    if (HAL_GPIO_ReadPin(KILL_SWITCH_Port, KILL_SWITCH_Pin) == GPIO_PIN_RESET) {
        if (!kill_active) {
            kill_active  = true;
            motor_state  = MOTOR_IDLE;
            Leadshine_Send_Stop();
            HAL_UART_Transmit(&huart2, (uint8_t *)"KILL\r\n", 6, 10);
        }
    } else {
        kill_active = false;
    }
}

/* ============================================================
 * COMMAND PARSER — USART2 (PC → STM32)
 * Commands: START:<rpm>  START  STOP  VEL:<rpm>  VELSET:<rpm>
 *           ACC:<val>    DEC:<val>  TARE_DISP
 * ============================================================ */
static void Process_Commands(void)
{
    while (cmd_queue_tail != cmd_queue_head) {
        char *cmd = cmd_queue[cmd_queue_tail];
        cmd_queue_tail = (cmd_queue_tail + 1) % CMD_QUEUE_SIZE;

        /* ── Reject commands if kill switch is active ── */
        if (kill_active && (strcmp(cmd, "STOP") != 0)) {
            continue;
        }

        if (strncmp(cmd, "START:", 6) == 0) {
            /* Atomic start with velocity: sets velocity and starts motor */
            int v = atoi(cmd + 6);
            if (v >= -1500 && v <= 1500)
                current_velocity = (int16_t)v;
            Leadshine_Send_Start(current_velocity);
            motor_state = MOTOR_RUNNING;
            last_stall_check = HAL_GetTick();
            last_stall_pos   = g_encoder_position;

        } else if (strcmp(cmd, "START") == 0) {
            Leadshine_Send_Start(current_velocity);
            motor_state = MOTOR_RUNNING;
            last_stall_check = HAL_GetTick();
            last_stall_pos   = g_encoder_position;

        } else if (strncmp(cmd, "VEL:", 4) == 0) {
            int v = atoi(cmd + 4);
            if (v >= -1500 && v <= 1500)
                current_velocity = (int16_t)v;

        } else if (strncmp(cmd, "ACC:", 4) == 0) {
            int a = atoi(cmd + 4);
            if (a >= 0 && a <= 3000)
                current_acc = (uint16_t)a;

        } else if (strncmp(cmd, "DEC:", 4) == 0) {
            int d = atoi(cmd + 4);
            if (d >= 0 && d <= 3000)
                current_dec = (uint16_t)d;

        } else if (strcmp(cmd, "STOP") == 0) {
            Leadshine_Send_Stop();
            motor_state = MOTOR_IDLE;

        } else if (strncmp(cmd, "VELSET:", 7) == 0) {
            /* Live velocity update while motor is running — only velocity register */
            int v = atoi(cmd + 7);
            if (v >= -1500 && v <= 1500) {
                current_velocity = (int16_t)v;
                if (motor_state == MOTOR_RUNNING) {
                    Leadshine_Send_VelOnly(current_velocity);
                    last_stall_check = HAL_GetTick();
                    last_stall_pos   = g_encoder_position;
                }
            }
        }
    }
}

/* ============================================================
 * ENCODER POLLING — USART1 DMA, 20 Hz
 * ONLY for homing. Does not fire while motor command is in-flight.
 * ============================================================ */
static void Process_Encoder(void)
{
    uint32_t tick = HAL_GetTick();

    if (!encoder_bus_busy && (tick - last_enc_poll >= ENC_POLL_MS)) {
        encoder_bus_busy = 1;
        last_enc_poll    = tick;
        HAL_UART_Transmit(&huart1, enc_request_frame, 8, 10);
    }

    /* Safety timeout — release bus if no response within 100 ms */
    if (encoder_bus_busy && (tick - last_enc_poll >= 100)) {
        encoder_bus_busy = 0;
        HAL_UARTEx_ReceiveToIdle_DMA(&huart1, enc_rx_buffer, ENC_RX_BUF_SIZE);
    }
}

/* ============================================================
 * LVDT SENSOR — USART3 IT, 20 Hz
 * Micro-Epsilon InduSensor proprietary binary protocol.
 * Request: 6-byte frame {0x10,0x7E,0x01,0x4C,0xCB,0x16}
 * Response: 17 bytes, SOF=0x68 0x0B 0x0B 0x68, EOF=0x16
 *           Displacement float at bytes [11..14]
 * ============================================================ */
static void Process_LVDT(void)
{
    // Process any complete frames in the buffer
    while (lvdt_rx_idx >= 17) {
        int frame_start = -1;
        for (int i = 0; i <= lvdt_rx_idx - 17; i++) {
            if (lvdt_rx_buf[i] == 0x68 && lvdt_rx_buf[i+1] == 0x0B &&
                lvdt_rx_buf[i+2] == 0x0B && lvdt_rx_buf[i+3] == 0x68 &&
                lvdt_rx_buf[i+16] == 0x16) {
                frame_start = i;
                break;
            }
        }

        if (frame_start >= 0) {
            uint8_t sum = 0;
            for (int j = 4; j <= 14; j++) {
                sum += lvdt_rx_buf[frame_start + j];
            }
            if (sum == lvdt_rx_buf[frame_start + 15]) {
                memcpy(&unscaled_val, &lvdt_rx_buf[frame_start + 7], 4);
                memcpy(&scaled_val, &lvdt_rx_buf[frame_start + 11], 4);
                g_displacement_mm = scaled_val - lvdt_tare_offset;
            }
            
            // Safely remove parsed frame from buffer
            int bytes_to_remove = frame_start + 17;
            HAL_NVIC_DisableIRQ(USART3_IRQn);
            memmove(lvdt_rx_buf, lvdt_rx_buf + bytes_to_remove, lvdt_rx_idx - bytes_to_remove);
            lvdt_rx_idx -= bytes_to_remove;
            HAL_NVIC_EnableIRQ(USART3_IRQn);
        } else {
            // No valid frame found in current buffer. If it's getting full, clear garbage.
            if (lvdt_rx_idx > 100) {
                int bytes_to_remove = lvdt_rx_idx - 16;
                HAL_NVIC_DisableIRQ(USART3_IRQn);
                memmove(lvdt_rx_buf, lvdt_rx_buf + bytes_to_remove, lvdt_rx_idx - bytes_to_remove);
                lvdt_rx_idx -= bytes_to_remove;
                HAL_NVIC_EnableIRQ(USART3_IRQn);
            }
            break;
        }
    }

    // Keep polling just in case it is NOT in continuous mode
    uint32_t tick = HAL_GetTick();
    if (tick - last_me_req_tick >= 50) { // 20Hz
        static uint8_t req[] = {0x10, 0x7E, 0x01, 0x4C, 0xCB, 0x16};
        HAL_UART_Transmit_IT(&huart3, req, 6);
        last_me_req_tick = tick;
    }
}

/* ============================================================
 * LOAD CELL — I2C1 IT, ~10 Hz
 * Protocol: send cmd 0x01 → wait 30 ms → read 4 bytes → LE float (kg)
 * ============================================================ */
static void Process_LoadCell(void)
{
    uint32_t now = HAL_GetTick();

    switch (lc_state) {
        case LC_IDLE:
            if (now - lc_poll_tick >= LC_POLL_MS) {
                lc_poll_tick = now;
                if (HAL_I2C_Master_Transmit_IT(&hi2c2, (uint16_t)(LC_SLAVE_ADDR << 1), &lc_cmd_byte, 1) == HAL_OK) {
                    lc_state = LC_TX_BUSY;
                }
                // If != HAL_OK, remain in LC_IDLE and try again next tick
            }
            break;
        case LC_WAIT_SENSOR:
            if (now - lc_proc_start >= LC_PROC_WAIT_MS) {
                if (HAL_I2C_Master_Receive_IT(&hi2c2, (uint16_t)(LC_SLAVE_ADDR << 1), lc_rx_buf, 4) == HAL_OK) {
                    lc_state = LC_RX_BUSY;
                } else {
                    lc_state = LC_IDLE; // Reset on immediate error
                }
            }
            break;
        case LC_TX_BUSY:
        case LC_RX_BUSY:
            break; /* Transitions handled in HAL callbacks */
    }
}

/* ============================================================
 * HAL CALLBACKS
 * ============================================================ */

/* USART2 (PC) byte-by-byte IT RX + USART3 (LVDT) byte accumulation */
void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance == USART2) {
        /* PC command line accumulator into FIFO ring buffer */
        if (uart2_rx_byte == '\n' || uart2_rx_byte == '\r') {
            uart2_rx_buf[uart2_rx_index] = '\0';
            if (uart2_rx_index > 0) {
                uint8_t next_head = (cmd_queue_head + 1) % CMD_QUEUE_SIZE;
                if (next_head != cmd_queue_tail) {
                    memcpy(cmd_queue[cmd_queue_head], uart2_rx_buf, uart2_rx_index + 1);
                    cmd_queue_head = next_head;
                }
                uart2_rx_index = 0;
            }
        } else if (uart2_rx_index < MAX_CMD_LEN - 1) {
            uart2_rx_buf[uart2_rx_index++] = uart2_rx_byte;
        }
        HAL_UART_Receive_IT(&huart2, &uart2_rx_byte, 1);
    }
    else if (huart->Instance == USART3) {
        lvdt_rx_buf[lvdt_rx_idx++] = lvdt_rx_byte;
        if(lvdt_rx_idx >= 256) lvdt_rx_idx = 0; // Prevent overflow
        HAL_UART_Receive_IT(&huart3, &lvdt_rx_byte, 1);
    }
}

/* USART1 DMA+Idle — encoder Modbus response */
void HAL_UARTEx_RxEventCallback(UART_HandleTypeDef *huart, uint16_t Size)
{
    if (huart->Instance == USART1) {
        if (Size == 9) {
            uint16_t rx_crc  = ((uint16_t)enc_rx_buffer[8] << 8) | enc_rx_buffer[7];
            uint16_t cal_crc = modbus_crc(enc_rx_buffer, 7);
            if (rx_crc == cal_crc) {
                uint16_t hi = ((uint16_t)enc_rx_buffer[3] << 8) | enc_rx_buffer[4];
                uint16_t lo = ((uint16_t)enc_rx_buffer[5] << 8) | enc_rx_buffer[6];
                g_encoder_position = (int32_t)(((uint32_t)hi << 16) | lo);
                g_enc_data_ready   = 1;
            }
        }
        /* Re-arm DMA and release bus */
        if (HAL_UARTEx_ReceiveToIdle_DMA(&huart1, enc_rx_buffer, ENC_RX_BUF_SIZE) == HAL_OK) {
            __HAL_DMA_DISABLE_IT(&hdma_usart1_rx, DMA_IT_HT);
        } else {
            g_enc_dma_restart = 1;
        }
        encoder_bus_busy = 0;
    }
}

/* USART1 error recovery */
void HAL_UART_ErrorCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance == USART1) {
        __HAL_UART_CLEAR_OREFLAG(huart);
        __HAL_UART_CLEAR_NEFLAG(huart);
        __HAL_UART_CLEAR_FEFLAG(huart);
        g_enc_dma_restart = 1;
    }
    else if (huart->Instance == USART3) {
        /* LVDT UART error recovery */
        __HAL_UART_CLEAR_OREFLAG(huart);
        __HAL_UART_CLEAR_NEFLAG(huart);
        __HAL_UART_CLEAR_FEFLAG(huart);
        // Restart the IT reception
        HAL_UART_Receive_IT(&huart3, &lvdt_rx_byte, 1);
    }
}

/* I2C2 TX complete — start 30 ms processing wait */
void HAL_I2C_MasterTxCpltCallback(I2C_HandleTypeDef *hi2c)
{
    if (hi2c->Instance == I2C2) {
        lc_proc_start = HAL_GetTick();
        lc_state      = LC_WAIT_SENSOR;
    }
}

/* I2C2 RX complete — decode little-endian float */
void HAL_I2C_MasterRxCpltCallback(I2C_HandleTypeDef *hi2c)
{
    if (hi2c->Instance == I2C2) {
        union { uint8_t b[4]; float f; } u;
        u.b[0] = lc_rx_buf[0]; u.b[1] = lc_rx_buf[1];
        u.b[2] = lc_rx_buf[2]; u.b[3] = lc_rx_buf[3];
        g_load_kg    = u.f;
        g_load_updated = true;
        lc_state       = LC_IDLE;
    }
}

/* I2C2 Error - recover state machine on NACK or bus error */
void HAL_I2C_ErrorCallback(I2C_HandleTypeDef *hi2c)
{
    if (hi2c->Instance == I2C2) {
        /* Reset state machine to try again next poll cycle */
        lc_state = LC_IDLE;
    }
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

  HAL_Init();

  /* USER CODE BEGIN Init */
  /* USER CODE END Init */

  SystemClock_Config();

  /* USER CODE BEGIN SysInit */
  /* USER CODE END SysInit */

  /* Initialize all configured peripherals */
  MX_GPIO_Init();
  MX_DMA_Init();           /* DMA MUST init before USART1 */
  MX_USART2_UART_Init();
  MX_USART1_UART_Init();
  MX_USART3_UART_Init();
  MX_I2C1_Init();
  MX_I2C2_Init();          /* Load Cell — PB10/PB3 */

  /* USER CODE BEGIN 2 */
  
  /* Fix interrupt priorities to prevent USART3 Overrun Errors */
  HAL_NVIC_SetPriority(USART3_IRQn, 0, 0); // Highest priority for 256kbaud LVDT
  HAL_NVIC_SetPriority(USART1_IRQn, 1, 0); // High priority for Encoder
  HAL_NVIC_SetPriority(I2C2_EV_IRQn, 2, 0); // Lower priority for Load Cell
  HAL_NVIC_SetPriority(USART2_IRQn, 3, 0); // Lowest priority for PC telemetry

  /* Arm USART2 (PC) interrupt RX */
  HAL_UART_Receive_IT(&huart2, &uart2_rx_byte, 1);

  /* Arm USART3 (LVDT) interrupt RX */
  HAL_UART_Receive_IT(&huart3, &lvdt_rx_byte, 1);

  /* Build encoder Modbus request: FC03, Slave 0x01, read 2 regs @ 0x602C */
  {
      uint8_t base[6] = {0x01, 0x03, 0x60, 0x2C, 0x00, 0x02};
      uint16_t crc    = modbus_crc(base, 6);
      memcpy(enc_request_frame, base, 6);
      enc_request_frame[6] = (uint8_t)(crc & 0xFF);
      enc_request_frame[7] = (uint8_t)(crc >> 8);
  }

  /* Arm USART1 DMA listener (encoder responses) */
  HAL_UARTEx_ReceiveToIdle_DMA(&huart1, enc_rx_buffer, ENC_RX_BUF_SIZE);
  __HAL_DMA_DISABLE_IT(&hdma_usart1_rx, DMA_IT_HT);

  /* USER CODE END 2 */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  while (1)
  {
    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */

    /* ── KILL SWITCH — absolute highest priority ── */
    Check_Kill_Switch();

    /* ── PC COMMAND PROCESSING ── */
    Process_Commands();

    /* ── LVDT SENSOR POLL (20 Hz internal timing) ── */
    Process_LVDT();

    /* ── LOAD CELL POLL (10 Hz internal timing) ── */
    Process_LoadCell();

    /* ── ENCODER POLL for homing (20 Hz, bus-arbitrated) ── */
    Process_Encoder();

    /* ── ENCODER NEW DATA: stream to PC for homing ── */
    if (g_enc_data_ready) {
        g_enc_data_ready = 0;
        char enc_msg[28];
        int  len = snprintf(enc_msg, sizeof(enc_msg),
                            "E:%ld\r\n", (long)g_encoder_position);
        HAL_UART_Transmit(&huart2, (uint8_t *)enc_msg, len, 10);
    }

    /* ── TELEMETRY TO PC: LOAD (N) + DISPLACEMENT (mm) at 20 Hz ── */
    {
        uint32_t now = HAL_GetTick();
        if (now - last_telemetry_tick >= TELEMETRY_MS) {
            last_telemetry_tick = now;
            /* Convert kg → N for GUI display */
            float load_n = g_load_kg * 9.81f;
            char  tx_buf[64];
            int   len = snprintf(tx_buf, sizeof(tx_buf),
                                 "LOAD:%.3f,DISP:%.4f\r\n",
                                 load_n, g_displacement_mm);
            HAL_UART_Transmit(&huart2, (uint8_t *)tx_buf, len, 10);
        }
    }

    /* ── STALL DETECTION (2.0s evaluation window with signed delta check) ── */
    if (motor_state == MOTOR_RUNNING && current_velocity != 0 && !kill_active) {
        uint32_t now = HAL_GetTick();
        if (now - last_stall_check >= 2000) {
            int32_t diff = g_encoder_position - last_stall_pos;
            if (diff < 0) diff = -diff;
            if (diff < 10) {
                /* Motor commanded to run but encoder moved < 10 pulses over 2.0 seconds */
                Leadshine_Send_Stop();
                motor_state = MOTOR_IDLE;
                HAL_UART_Transmit(&huart2, (uint8_t *)"ERR:STALL\r\n", 11, 10);
            }
            last_stall_pos   = g_encoder_position;
            last_stall_check = now;
        }
    } else {
        last_stall_check = HAL_GetTick();
        last_stall_pos   = g_encoder_position;
    }

    /* ── DMA SELF-RECOVERY (USART1) ── */
    if (g_enc_dma_restart) {
        HAL_UART_AbortReceive(&huart1);
        __HAL_UART_CLEAR_OREFLAG(&huart1);
        __HAL_UART_CLEAR_NEFLAG(&huart1);
        __HAL_UART_CLEAR_FEFLAG(&huart1);
        if (HAL_UARTEx_ReceiveToIdle_DMA(&huart1,
                enc_rx_buffer, ENC_RX_BUF_SIZE) == HAL_OK) {
            __HAL_DMA_DISABLE_IT(&hdma_usart1_rx, DMA_IT_HT);
            g_enc_dma_restart = 0;
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

  __HAL_RCC_PWR_CLK_ENABLE();
  __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE3);

  RCC_OscInitStruct.OscillatorType      = RCC_OSCILLATORTYPE_HSI;
  RCC_OscInitStruct.HSIState            = RCC_HSI_ON;
  RCC_OscInitStruct.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
  RCC_OscInitStruct.PLL.PLLState        = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource       = RCC_PLLSOURCE_HSI;
  RCC_OscInitStruct.PLL.PLLM            = 16;
  RCC_OscInitStruct.PLL.PLLN            = 336;
  RCC_OscInitStruct.PLL.PLLP            = RCC_PLLP_DIV4;
  RCC_OscInitStruct.PLL.PLLQ            = 2;
  RCC_OscInitStruct.PLL.PLLR            = 2;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK) Error_Handler();

  RCC_ClkInitStruct.ClockType      = RCC_CLOCKTYPE_HCLK | RCC_CLOCKTYPE_SYSCLK |
                                     RCC_CLOCKTYPE_PCLK1 | RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource   = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.AHBCLKDivider  = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV2;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;
  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_2) != HAL_OK) Error_Handler();
}

/**
  * @brief I2C1 Initialization Function
  */
static void MX_I2C1_Init(void)
{
  /* USER CODE BEGIN I2C1_Init 0 */
  /* USER CODE END I2C1_Init 0 */
  /* USER CODE BEGIN I2C1_Init 1 */
  /* USER CODE END I2C1_Init 1 */
  hi2c1.Instance             = I2C1;
  hi2c1.Init.ClockSpeed      = 100000;
  hi2c1.Init.DutyCycle       = I2C_DUTYCYCLE_2;
  hi2c1.Init.OwnAddress1     = 0;
  hi2c1.Init.AddressingMode  = I2C_ADDRESSINGMODE_7BIT;
  hi2c1.Init.DualAddressMode = I2C_DUALADDRESS_DISABLE;
  hi2c1.Init.OwnAddress2     = 0;
  hi2c1.Init.GeneralCallMode = I2C_GENERALCALL_DISABLE;
  hi2c1.Init.NoStretchMode   = I2C_NOSTRETCH_DISABLE;
  if (HAL_I2C_Init(&hi2c1) != HAL_OK) Error_Handler();
  /* USER CODE BEGIN I2C1_Init 2 */
  /* USER CODE END I2C1_Init 2 */
}

/**
  * @brief I2C2 Initialization Function (Load Cell — PB10/PB3)
  */
static void MX_I2C2_Init(void)
{
  /* USER CODE BEGIN I2C2_Init 0 */
  /* USER CODE END I2C2_Init 0 */
  /* USER CODE BEGIN I2C2_Init 1 */
  /* USER CODE END I2C2_Init 1 */
  hi2c2.Instance             = I2C2;
  hi2c2.Init.ClockSpeed      = 100000;
  hi2c2.Init.DutyCycle       = I2C_DUTYCYCLE_2;
  hi2c2.Init.OwnAddress1     = 0;
  hi2c2.Init.AddressingMode  = I2C_ADDRESSINGMODE_7BIT;
  hi2c2.Init.DualAddressMode = I2C_DUALADDRESS_DISABLE;
  hi2c2.Init.OwnAddress2     = 0;
  hi2c2.Init.GeneralCallMode = I2C_GENERALCALL_DISABLE;
  hi2c2.Init.NoStretchMode   = I2C_NOSTRETCH_DISABLE;
  if (HAL_I2C_Init(&hi2c2) != HAL_OK) Error_Handler();
  /* USER CODE BEGIN I2C2_Init 2 */
  /* USER CODE END I2C2_Init 2 */
}

/**
  * @brief USART1 Initialization (Leedshine motor — 115200, 8N2)
  */
static void MX_USART1_UART_Init(void)
{
  /* USER CODE BEGIN USART1_Init 0 */
  /* USER CODE END USART1_Init 0 */
  /* USER CODE BEGIN USART1_Init 1 */
  /* USER CODE END USART1_Init 1 */
  huart1.Instance          = USART1;
  huart1.Init.BaudRate     = 115200;
  huart1.Init.WordLength   = UART_WORDLENGTH_8B;
  huart1.Init.StopBits     = UART_STOPBITS_2;   /* Modbus requires 2 stop bits */
  huart1.Init.Parity       = UART_PARITY_NONE;
  huart1.Init.Mode         = UART_MODE_TX_RX;
  huart1.Init.HwFlowCtl    = UART_HWCONTROL_NONE;
  huart1.Init.OverSampling = UART_OVERSAMPLING_16;
  if (HAL_UART_Init(&huart1) != HAL_OK) Error_Handler();
  /* USER CODE BEGIN USART1_Init 2 */
  /* USER CODE END USART1_Init 2 */
}

/**
  * @brief USART2 Initialization (PC host link — 115200, 8N1)
  */
static void MX_USART2_UART_Init(void)
{
  /* USER CODE BEGIN USART2_Init 0 */
  /* USER CODE END USART2_Init 0 */
  /* USER CODE BEGIN USART2_Init 1 */
  /* USER CODE END USART2_Init 1 */
  huart2.Instance          = USART2;
  huart2.Init.BaudRate     = 115200;
  huart2.Init.WordLength   = UART_WORDLENGTH_8B;
  huart2.Init.StopBits     = UART_STOPBITS_1;
  huart2.Init.Parity       = UART_PARITY_NONE;
  huart2.Init.Mode         = UART_MODE_TX_RX;
  huart2.Init.HwFlowCtl    = UART_HWCONTROL_NONE;
  huart2.Init.OverSampling = UART_OVERSAMPLING_16;
  if (HAL_UART_Init(&huart2) != HAL_OK) Error_Handler();
  /* USER CODE BEGIN USART2_Init 2 */
  /* USER CODE END USART2_Init 2 */
}

/**
  * @brief USART3 Initialization (Micro-Epsilon LVDT — 256000, 9-bit Even Parity)
  */
static void MX_USART3_UART_Init(void)
{
  /* USER CODE BEGIN USART3_Init 0 */
  /* USER CODE END USART3_Init 0 */
  /* USER CODE BEGIN USART3_Init 1 */
  /* USER CODE END USART3_Init 1 */
  huart3.Instance          = USART3;
  huart3.Init.BaudRate     = 256000;
  huart3.Init.WordLength   = UART_WORDLENGTH_9B;
  huart3.Init.StopBits     = UART_STOPBITS_1;
  huart3.Init.Parity       = UART_PARITY_EVEN;
  huart3.Init.Mode         = UART_MODE_TX_RX;
  huart3.Init.HwFlowCtl    = UART_HWCONTROL_NONE;
  huart3.Init.OverSampling = UART_OVERSAMPLING_16;
  if (HAL_UART_Init(&huart3) != HAL_OK) Error_Handler();
  /* USER CODE BEGIN USART3_Init 2 */
  /* USER CODE END USART3_Init 2 */
}

/**
  * @brief DMA Initialization (DMA2 Stream2 Ch4 — USART1 RX for encoder)
  */
static void MX_DMA_Init(void)
{
  __HAL_RCC_DMA2_CLK_ENABLE();

  /* DMA2_Stream2 — USART1 RX */
  hdma_usart1_rx.Instance                 = DMA2_Stream2;
  hdma_usart1_rx.Init.Channel             = DMA_CHANNEL_4;
  hdma_usart1_rx.Init.Direction           = DMA_PERIPH_TO_MEMORY;
  hdma_usart1_rx.Init.PeriphInc           = DMA_PINC_DISABLE;
  hdma_usart1_rx.Init.MemInc              = DMA_MINC_ENABLE;
  hdma_usart1_rx.Init.PeriphDataAlignment = DMA_PDATAALIGN_BYTE;
  hdma_usart1_rx.Init.MemDataAlignment    = DMA_MDATAALIGN_BYTE;
  hdma_usart1_rx.Init.Mode                = DMA_NORMAL;
  hdma_usart1_rx.Init.Priority            = DMA_PRIORITY_LOW;
  hdma_usart1_rx.Init.FIFOMode            = DMA_FIFOMODE_DISABLE;
  if (HAL_DMA_Init(&hdma_usart1_rx) != HAL_OK) Error_Handler();

  __HAL_LINKDMA(&huart1, hdmarx, hdma_usart1_rx);

  HAL_NVIC_SetPriority(DMA2_Stream2_IRQn, 0, 0);
  HAL_NVIC_EnableIRQ(DMA2_Stream2_IRQn);
}

/**
  * @brief GPIO Initialization
  */
static void MX_GPIO_Init(void)
{
  GPIO_InitTypeDef GPIO_InitStruct = {0};
  /* USER CODE BEGIN MX_GPIO_Init_1 */
  /* USER CODE END MX_GPIO_Init_1 */

  __HAL_RCC_GPIOC_CLK_ENABLE();
  __HAL_RCC_GPIOH_CLK_ENABLE();
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOB_CLK_ENABLE();

  /* Default output levels */
  HAL_GPIO_WritePin(LD2_GPIO_Port, LD2_Pin, GPIO_PIN_RESET);

  /* Board user button (B1) */
  GPIO_InitStruct.Pin  = B1_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_IT_FALLING;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  HAL_GPIO_Init(B1_GPIO_Port, &GPIO_InitStruct);

  /* LED2 */
  GPIO_InitStruct.Pin   = LD2_Pin;
  GPIO_InitStruct.Mode  = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull  = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(LD2_GPIO_Port, &GPIO_InitStruct);

  /* USER CODE BEGIN MX_GPIO_Init_2 */

  /* Kill switch — PB4, input with pull-up (active LOW, highest priority) */
  GPIO_InitStruct.Pin  = KILL_SWITCH_Pin;   /* GPIO_PIN_4 */
  GPIO_InitStruct.Mode = GPIO_MODE_INPUT;
  GPIO_InitStruct.Pull = GPIO_PULLUP;
  HAL_GPIO_Init(KILL_SWITCH_Port, &GPIO_InitStruct);  /* GPIOB */

  /* USER CODE END MX_GPIO_Init_2 */
}

/* USER CODE BEGIN 4 */
/* USER CODE END 4 */

/**
  * @brief  Error Handler
  */
void Error_Handler(void)
{
  /* USER CODE BEGIN Error_Handler_Debug */
  __disable_irq();
  while (1) {}
  /* USER CODE END Error_Handler_Debug */
}

#ifdef USE_FULL_ASSERT
void assert_failed(uint8_t *file, uint32_t line)
{
  /* USER CODE BEGIN 6 */
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */
