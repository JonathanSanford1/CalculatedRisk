// Unit tests for the app's data models. None of these need Firebase.
//
// Run with:  flutter test

import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:calculated_risk/models/hedge_goal.dart';
import 'package:calculated_risk/models/opportunity.dart';
import 'package:calculated_risk/models/profit_boost.dart';
import 'package:calculated_risk/models/rounding.dart';

void main() {
  group('RoundingMode.fromName', () {
    test('reads the current names', () {
      expect(RoundingMode.fromName('small'), RoundingMode.small);
      expect(RoundingMode.fromName('medium'), RoundingMode.medium);
      expect(RoundingMode.fromName('large'), RoundingMode.large);
    });

    test('maps the names older app versions saved', () {
      expect(RoundingMode.fromName('none'), RoundingMode.small);
      expect(RoundingMode.fromName('light'), RoundingMode.medium);
      expect(RoundingMode.fromName('heavy'), RoundingMode.large);
    });

    test('falls back to small', () {
      expect(RoundingMode.fromName(null), RoundingMode.small);
      expect(RoundingMode.fromName('nonsense'), RoundingMode.small);
    });
  });

  group('HedgeGoal.fromName', () {
    test('reads both goals', () {
      expect(HedgeGoal.fromName('guaranteed'), HedgeGoal.guaranteed);
      expect(HedgeGoal.fromName('max'), HedgeGoal.max);
    });

    test('falls back to guaranteed', () {
      expect(HedgeGoal.fromName(null), HedgeGoal.guaranteed);
      expect(HedgeGoal.fromName('nonsense'), HedgeGoal.guaranteed);
    });
  });

  group('PropType', () {
    test('offers each league only what the backend can search', () {
      expect(PropType.optionsFor(BetType.nfl), [
        PropType.moneyline,
        PropType.spread,
        PropType.total,
        PropType.receivingYards,
      ]);
      expect(PropType.optionsFor(BetType.nba), [
        PropType.moneyline,
        PropType.spread,
        PropType.total,
        PropType.points,
      ]);
      expect(PropType.optionsFor(BetType.tennis), [
        PropType.moneyline,
        PropType.spread,
        PropType.total,
      ]);
    });

    test('soccer has no moneyline (a draw makes it unhedgeable)', () {
      expect(PropType.optionsFor(BetType.epl), [
        PropType.spread,
        PropType.total,
        PropType.bothTeamsToScore,
        PropType.shotsOnTarget,
      ]);
    });

    test('leagues that are not searched offer nothing', () {
      expect(PropType.optionsFor(BetType.golf), isEmpty);
    });

    test('listFrom ignores missing and unknown names', () {
      expect(PropType.listFrom(null), isEmpty);
      expect(PropType.listFrom(['total', 'touchdownScorer']), [PropType.total]);
    });

    test('describe reads naturally', () {
      expect(PropType.describe([]), '');
      expect(PropType.describe([PropType.receivingYards]),
          'player receiving yards');
      expect(PropType.describe([PropType.moneyline, PropType.spread]),
          'moneyline or spread');
      expect(
          PropType.describe(
              [PropType.moneyline, PropType.spread, PropType.total]),
          'moneyline, spread or total (over/under)');
    });
  });

  group('ProfitBoost.fromFirestore', () {
    Map<String, dynamic> boostData({Object? propTypes}) => {
          'bookmaker': 'fanduel',
          'percentBoost': 30,
          'betType': 'nfl',
          'minOdds': -200,
          'maxOdds': 300,
          'validFrom': Timestamp.fromDate(DateTime(2026, 10, 10, 9)),
          'validUntil': Timestamp.fromDate(DateTime(2026, 10, 11, 23)),
          'maxBet': 25,
          'propTypes': ?propTypes,
        };

    test('reads the basic fields', () {
      final boost = ProfitBoost.fromFirestore('b1', boostData());
      expect(boost.sportsbook, Sportsbook.fanduel);
      expect(boost.betType, BetType.nfl);
      expect(boost.percentBoost, 30);
      expect(boost.maxBet, 25);
      expect(boost.minOdds, -200);
      expect(boost.maxOdds, 300);
    });

    test('a boost saved without bet types applies to any bet', () {
      final boost = ProfitBoost.fromFirestore('b1', boostData());
      expect(boost.propTypes, isEmpty);
      expect(boost.hasPropFilter, isFalse);
    });

    test('reads the bet types a boost is limited to', () {
      final boost = ProfitBoost.fromFirestore(
          'b1', boostData(propTypes: ['moneyline', 'total']));
      expect(boost.propTypes, [PropType.moneyline, PropType.total]);
      expect(boost.hasPropFilter, isTrue);
    });
  });

  group('Opportunity.fromMap', () {
    test('a hedge saved before stake versions existed gets one safest version',
        () {
      final bet = Opportunity.fromMap({
        'id': 'x',
        'betType': 'nfl',
        'game': 'Away @ Home',
        'commenceTime': Timestamp.fromDate(DateTime(2026, 10, 12, 18)),
        'marketLabel': 'moneyline',
        'legs': [
          {
            'bookmaker': 'draftkings',
            'selection': 'Home moneyline',
            'odds': -150,
            'stake': 25.0,
            'payout': 45.0,
            'boostPercent': 50,
            'boostId': 'b1',
          },
          {
            'bookmaker': 'fanduel',
            'selection': 'Away moneyline',
            'odds': 140,
            'stake': 28.0,
            'payout': 67.2,
            'boostPercent': 0,
            'boostId': null,
          },
        ],
        'totalStake': 53.0,
        'guaranteedProfit': 2.0,
        'boostIds': ['b1'],
      });

      expect(bet.versions, hasLength(1));
      expect(bet.safest.key, 'safest');
      expect(bet.safest.stakes, [25.0, 28.0]);
      expect(bet.guaranteedProfit, 2.0);
      expect(bet.mostUpside.maxProfit, 2.0); // defaults to the guaranteed profit
      expect(bet.legs.first.isBoosted, isTrue);
      expect(bet.legs.last.isBoosted, isFalse);
    });
  });
}