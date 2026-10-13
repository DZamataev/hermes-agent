// Labels and descriptions for the display fields on Settings → Chat, composed by fr.ts.
export const frDisplaySettings = {
  labels: {
    personality: 'Personnalité',
    showReasoning: 'Blocs de raisonnement',
    composerPredictions: 'Prédiction du prochain message'
  },
  descriptions: {
    personality: "Style par défaut de l'assistant pour les nouvelles sessions.",
    showReasoning: 'Afficher les sections de raisonnement quand le backend les fournit.',
    composerPredictions:
      "Après chaque réponse, afficher un prochain message prédit en texte estompé dans le compositeur vide (Tab ou flèche droite l'accepte). Coûte une petite requête, en grande partie mise en cache, par tour sur le modèle de la session."
  }
}
