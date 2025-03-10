// Copyright (c) 2023-2024 Canonical Ltd
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with this program.  If not, see <http://www.gnu.org/licenses/>.

package pathutil

import (
	"os"
	"path/filepath"
)

// GetDataPath returns directory for MAAS data files depending on
// MAAS installation type (snap or deb).
func GetDataPath(path string) string {
	dataDir := os.Getenv("SNAP_DATA")

	if dataDir != "" {
		return filepath.Join(filepath.Clean(dataDir), path)
	}

	return filepath.Join("/var/lib/maas", path)
}

func GetMAASDataPath(path string) string {
	maasDir := os.Getenv("MAAS_DATA")

	if maasDir != "" {
		return filepath.Join(filepath.Clean(maasDir), path)
	}

	return filepath.Join("/var/lib/maas", path)
}
