using System.Windows;

namespace Athena;

/// <summary>ATHENA — native Windows shell (N1). Entry point only.</summary>
public partial class App : Application
{
    public App()
    {
        DispatcherUnhandledException += (_, e) =>
        {
            MessageBox.Show(
                $"Something went wrong:\n{e.Exception.Message}",
                "ATHENA",
                MessageBoxButton.OK,
                MessageBoxImage.Error);
            e.Handled = true;
        };
    }
}
