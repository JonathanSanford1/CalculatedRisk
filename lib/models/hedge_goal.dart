/// What hedges and the Best plan are ranked by.
/// Names must match OBJECTIVES in functions/main.py.
enum HedgeGoal {
  guaranteed('Most guaranteed', 'Ranked by the profit you get no matter what.'),
  max('Highest upside', 'Ranked by the most you could win (still never a loss).');

  const HedgeGoal(this.label, this.description);
  final String label;
  final String description;

  static HedgeGoal fromName(Object? name) =>
      HedgeGoal.values.asNameMap()[name] ?? HedgeGoal.guaranteed;
}
