// Labels and descriptions for the display fields on Settings → Chat, composed by es.ts.
export const esDisplaySettings = {
  labels: {
    personality: 'Personalidad',
    showReasoning: 'Bloques de razonamiento',
    composerPredictions: 'Predicción del siguiente mensaje'
  },
  descriptions: {
    personality: 'Estilo predeterminado del asistente para sesiones nuevas.',
    showReasoning: 'Muestra secciones de razonamiento cuando el backend las proporcione.',
    composerPredictions:
      'Tras cada respuesta, muestra un siguiente mensaje previsto como texto atenuado en el compositor vacío (Tab o flecha derecha lo acepta). Cuesta una solicitud pequeña y casi toda en caché por turno en el modelo de la sesión.'
  }
}
