// Labels and descriptions for the display fields on Settings → Chat, composed by ja.ts.
export const jaDisplaySettings = {
  labels: {
    personality: '人格',
    showReasoning: '推論ブロック',
    composerPredictions: '次のメッセージの予測'
  },
  descriptions: {
    personality: '新しいセッションのデフォルトのアシスタントスタイルです。',
    showReasoning: 'バックエンドが推論内容を提供したときに表示します。',
    composerPredictions:
      '各返信の後、空の入力欄に次のメッセージの予測を薄い文字で表示します（Tab または右矢印で確定）。セッションのモデルで、ターンごとに大部分がキャッシュされる小さなリクエストが 1 回発生します。'
  }
}
