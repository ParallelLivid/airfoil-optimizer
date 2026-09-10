import sys
import os
import config
try:
    import xfoil_runner
except ImportError as exc:
    print(f'ERROR: Could not load dependency {exc.name}. '
          f'Install dependencies with: "{sys.executable}" -m pip install -r '
          f'"{config.PROJECT_DIR / "requirements.txt"}"')
    sys.exit(1)

# --- Helper Functions ---

def get_simulation_inputs():
    """Common logic for getting Re and Alpha range inputs."""
    while True:
        try:
            rey_number = int(input("Enter Reynolds Number (e.g., 300000): ").strip())
            alpha_start = float(input("Enter Alpha Start Angle (e.g., -5.0): ").strip())
            alpha_end = float(input("Enter Alpha End Angle (e.g., 15.0): ").strip())
            alpha_inc = float(input("Enter Alpha Increment (e.g., 0.5): ").strip())

            config.validate_simulation_inputs(rey_number, alpha_start, alpha_end, alpha_inc)
            return rey_number, alpha_start, alpha_end, alpha_inc
        except (ValueError, OverflowError) as exc:
            print(f"Invalid input: {exc}")

# --- Menu Functions ---

def settings_menu():
    """Allows the user to modify global simulation settings and saves them persistently."""

    while True:
        print("\n\n*** Simulation Settings ***")
        print(f"1. Max Iterations (Current: {config.MAX_ITERATIONS})")
        print(f"2. Panel Nodes (Current: {config.PANEL_NODES})")
        print("3. Save and Back to Main Menu")
        print(f"4. Timeout in seconds (Current: {config.TIMEOUT_SECONDS})")

        setting_choice = input("Select setting to change (1-4): ").strip()

        if setting_choice == '1':
            try:
                new_iter = input("Enter new Max Iterations: ")
                if new_iter:
                    config.validate_settings({**config.snapshot_settings(), 'max_iterations': int(new_iter)})
                    config.MAX_ITERATIONS = int(new_iter)
                    print(f"Max Iterations set to {config.MAX_ITERATIONS}.")
                else:
                    print("Invalid input. Must be a positive integer.")
            except ValueError as exc:
                print(f"Invalid input: {exc}")

        elif setting_choice == '2':
            try:
                new_panels = input("Enter new Panel Nodes (e.g., 160, 240): ")
                if new_panels:
                    config.validate_settings({**config.snapshot_settings(), 'panel_nodes': int(new_panels)})
                    config.PANEL_NODES = int(new_panels)
                    print(f"Panel Nodes set to {config.PANEL_NODES}.")
                else:
                    print("Invalid input. Must be a positive even integer.")
            except ValueError as exc:
                print(f"Invalid input: {exc}")

        elif setting_choice == '3':
            if config.save_settings():
                break
        elif setting_choice == '4':
            try:
                timeout = float(input('Enter timeout in seconds: '))
                config.validate_settings({**config.snapshot_settings(), 'timeout_seconds': timeout})
                config.TIMEOUT_SECONDS = timeout
            except ValueError as exc:
                print(f'Invalid input: {exc}')
        else:
            print("Invalid choice. Please enter 1, 2, 3, or 4.")

def naca_analysis_menu():
    """Menu for single NACA airfoil analysis."""
    print("\n\n*** Single NACA Airfoil Analysis ***")

    while True:
        naca_code = input("Enter NACA 4-digit code (e.g., 2412): ").strip()
        if config.valid_naca_code(naca_code):
            break
        print("Invalid NACA code. Must be a 4-digit number.")

    rey_number, alpha_start, alpha_end, alpha_inc = get_simulation_inputs()

    try:
        xfoil_runner.run_xfoil_naca(naca_code, rey_number, alpha_start, alpha_end, alpha_inc)
    except Exception as e:
        print(f"An error occurred during NACA analysis: {e}")

def custom_analysis_menu():
    """Menu for single custom airfoil analysis."""
    print("\n\n*** Single Custom Airfoil Analysis ***")

    while True:
        coords_file_path = os.path.expanduser(input("Enter the path to the airfoil coordinate file (.dat/.txt): ").strip().strip('"'))
        if os.path.isfile(coords_file_path):
            break
        print(f"File not found: {coords_file_path}")

    rey_number, alpha_start, alpha_end, alpha_inc = get_simulation_inputs()

    try:
        xfoil_runner.run_xfoil_custom(coords_file_path, rey_number, alpha_start, alpha_end, alpha_inc)
    except Exception as e:
        print(f"An error occurred during custom airfoil analysis: {e}")

def batch_analysis_menu():
    """Menu for NACA batch comparison and optimization."""

    print("\n*** NACA Batch Comparison Menu ***")

    while True:
        naca_input = input("Enter NACA 4-digit codes, separated by commas (e.g., 2412, 4415): ").strip()
        naca_codes = [code.strip() for code in naca_input.split(',')]
        invalid = [code or '(empty entry)' for code in naca_codes if not config.valid_naca_code(code)]
        if invalid:
            print(f"Invalid NACA entries: {', '.join(invalid)}. Please correct the list.")
            continue

        # Break loop if valid codes are found
        break

    print(f"\nProceeding with {len(naca_codes)} airfoils...")

    rey_number, alpha_start, alpha_end, alpha_inc = get_simulation_inputs()

    try:
        xfoil_runner.run_xfoil_batch(naca_codes, rey_number, alpha_start, alpha_end, alpha_inc)
    except Exception as e:
        print(f"A critical error occurred during batch analysis: {e}")

# --- Main Execution Loop ---

def main():

    config.load_settings()

    while True:
        print("\n==================================")
        print("XFOIL Automation Main Menu")
        print("==================================")
        print("1. NACA Airfoil Analysis (Single, Full Report)")
        print("2. Custom Airfoil Analysis (Single, Full Report)")
        print("3. Simulation Settings")
        print("4. NACA Batch Comparison & Optimization")
        print("5. Exit Script")
        print("----------------------------------")

        main_choice = input("Enter your choice (1-5): ").strip()

        if main_choice == '1':
            naca_analysis_menu()
        elif main_choice == '2':
            custom_analysis_menu()
        elif main_choice == '3':
            settings_menu()
        elif main_choice == '4':
            batch_analysis_menu()
        elif main_choice == '5':
            print("Exiting XFOIL Automation Script. Goodbye!")
            return 0
        else:
            print("Invalid choice. Please enter a number between 1 and 5.")


def cli():
    try:
        return main()
    except EOFError:
        print('\nEnd of input. Goodbye!')
        return 0
    except KeyboardInterrupt:
        print('\nCancelled. Solver processes have been stopped.')
        return 130


if __name__ == '__main__':
    sys.exit(cli())
