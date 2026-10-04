import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/material.dart';

import '../services/boost_repository.dart';
import '../widgets/error_view.dart';
import 'main_shell.dart';

/// Signs in (anonymously, once per install), then shows the app.
class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  late Future<BoostRepository> _repositoryFuture;

  @override
  void initState() {
    super.initState();
    _repositoryFuture = _connect();
  }

  Future<BoostRepository> _connect() async {
    final auth = FirebaseAuth.instance;
    final user = auth.currentUser ?? (await auth.signInAnonymously()).user!;
    return BoostRepository(uid: user.uid);
  }

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<BoostRepository>(
      future: _repositoryFuture,
      builder: (context, snapshot) {
        if (snapshot.hasData) {
          return MainShell(repository: snapshot.data!);
        }
        return Scaffold(
          appBar: AppBar(title: const Text('CalculatedRisk')),
          body: snapshot.hasError
              ? ErrorView(
                  message: 'Couldn\'t connect to your account.',
                  details: '${snapshot.error}',
                  onRetry: () =>
                      setState(() => _repositoryFuture = _connect()),
                )
              : const Center(child: CircularProgressIndicator()),
        );
      },
    );
  }
}
