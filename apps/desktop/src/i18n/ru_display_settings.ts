// Labels and descriptions for the display fields on Settings → Chat, composed by ru.ts.
export const ruDisplaySettings = {
  labels: {
    personality: 'Личность',
    showReasoning: 'Блоки рассуждений',
    composerPredictions: 'Предсказание следующего сообщения'
  },
  descriptions: {
    personality: 'Стиль ассистента по умолчанию для новых сеансов.',
    showReasoning: 'Показывать блоки рассуждений, когда бэкенд их предоставляет.',
    composerPredictions:
      'После каждого ответа показывать в пустом композере серым текстом предсказанное следующее сообщение (Tab или стрелка вправо принимает). Стоит один небольшой, почти полностью кэшированный запрос за ход на модели сессии.'
  }
}
