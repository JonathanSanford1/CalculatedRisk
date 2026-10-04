import 'package:flutter/material.dart';

import '../models/profit_boost.dart';

/// Each sportsbook's color: DraftKings is the green section, FanDuel the blue.
extension SportsbookStyle on Sportsbook {
  MaterialColor get color => switch (this) {
        Sportsbook.draftkings => Colors.green,
        Sportsbook.fanduel => Colors.blue,
      };
}
