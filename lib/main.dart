import 'package:flutter/material.dart';

import 'screens/home_screen.dart';

void main() => runApp(const CalculatedRiskApp());

class CalculatedRiskApp extends StatelessWidget {
  const CalculatedRiskApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'CalculatedRisk',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        colorSchemeSeed: Colors.blueGrey,
      ),
      home: const HomeScreen(),
    );
  }
}