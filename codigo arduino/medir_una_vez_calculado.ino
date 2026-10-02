#include <Wire.h>
#include <INA226_WE.h>

#define I2C_ADDRESS 0x40
INA226_WE ina226 = INA226_WE(I2C_ADDRESS);

const float SHUNT_OHMS      = 0.1;   // shunt interno de fabrica del modulo
const float CORRIENTE_MAX_A = 1.0;   // rango de calibracion
const int   NUM_MUESTRAS    = 10;    // cuantas lecturas promediar

const float R_CARGA_OHMS = 92.4;

void setup() {
  Serial.begin(9600);
  Wire.begin();

  if (!ina226.init()) {
    Serial.println("ERROR: no se detecta el INA226. Revisa conexiones I2C.");
    while (true);
  }

  ina226.setResistorRange(SHUNT_OHMS, CORRIENTE_MAX_A);
  ina226.setAverage(INA226_AVERAGE_16);
  ina226.setConversionTime(INA226_CONV_TIME_1100);

  delay(500); // pausa para que el sensor se estabilice

  // Promedia varias muestras de CORRIENTE (el canal que si funciona)
  float sumaI = 0;
  for (int i = 0; i < NUM_MUESTRAS; i++) {
    sumaI += ina226.getCurrent_mA() * 1000.0; // a uA
    delay(200);
  }
  float corriente_uA = sumaI / NUM_MUESTRAS;

  // Ley de Ohm: V = I * R
  // corriente_uA esta en microamperios -> pasar a amperios para el calculo
  float corriente_A = corriente_uA / 1000000.0;
  float voltaje_V   = corriente_A * R_CARGA_OHMS;

  // Potencia: P = V * I
  float potencia_uW = voltaje_V * corriente_uA;

  Serial.println();
  Serial.println("===== RESULTADO DE LA MEDICION =====");
  Serial.print("Voltaje (calculado): ");
  Serial.print(voltaje_V, 4);
  Serial.println(" V");
  Serial.print("Corriente (medida):  ");
  Serial.print(corriente_uA, 3);
  Serial.println(" uA (microamperios)");
  Serial.print("Potencia (calculada): ");
  Serial.print(potencia_uW, 3);
  Serial.println(" uW (microwatts)");
  Serial.print("   [R de carga usada: ");
  Serial.print(R_CARGA_OHMS, 1);
  Serial.println(" ohm]");
}

void loop() {
  
}
