// Labels and descriptions for the display fields on Settings → Chat, composed by de.ts.
export const deDisplaySettings = {
  labels: {
    personality: 'Persönlichkeit',
    showReasoning: 'Denkblöcke',
    composerPredictions: 'Vorhersage der nächsten Nachricht'
  },
  descriptions: {
    personality: 'Standard-Assistentenstil für neue Sessions.',
    showReasoning: 'Denkabschnitte anzeigen, wenn das Backend sie liefert.',
    composerPredictions:
      'Nach jeder Antwort eine vorhergesagte nächste Nachricht als blassen Text im leeren Eingabefeld anzeigen (Tab oder Pfeil rechts übernimmt sie). Kostet pro Durchgang eine kleine, größtenteils gecachte Anfrage an das Session-Modell.'
  }
}
