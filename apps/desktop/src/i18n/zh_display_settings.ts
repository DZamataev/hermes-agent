// Labels and descriptions for the display fields on Settings → Chat, composed by zh.ts.
export const zhDisplaySettings = {
  labels: {
    personality: '人格',
    showReasoning: '推理过程块',
    composerPredictions: '下一条消息预测'
  },
  descriptions: {
    personality: '新会话的默认助手风格。',
    showReasoning: '当后端提供推理内容时予以显示。',
    composerPredictions:
      '每次回复后，在空的输入框中以浅色文字显示预测的下一条消息（按 Tab 或右方向键采用）。每轮会在会话模型上产生一次小型且大部分命中缓存的请求。'
  }
}
