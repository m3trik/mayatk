groupName = "G"

-- return all groups names in the hierarchy (including tiles's names)
function GetGroupNames(path, groupNames)
	itemNames = ZomItemNames(path .. ".Children")
	for i,itemName in pairs(itemNames) do
		groupNames[itemName] = true
		GetGroupNames(path .. ".Children." .. itemName, groupNames)
	end
end

-- get all group names
groupNames = {}
gNames = GetGroupNames("Lib.Mesh.RootGroup", groupNames)

-- find a free group name
i = 0
while groupNames[groupName..i] ~= nil do
	i = i + 1
end
groupName = groupName..i
print (groupName)

-- return the island IDs that has at least one edge selected
function IslandsSelectedByEdges()
	local polyIDToIslandIDs = ZomGet("Lib.Mesh.PolyIDToIslandIDs")
	local polyEdgeTable = ZomGet("Lib.Mesh.SelectedPolyEdgeIDs")
	local tmp = {}
	for key,value in pairs(polyEdgeTable) do
		if key%2 ~= 0 then
			isID = polyIDToIslandIDs[value]
			if tmp[isID] == nil then
				tmp[isID] = true
			end
		end
	end
	local islandIDs = {}
	for k,v in pairs(tmp) do
		table.insert(islandIDs, k)
	end
	return islandIDs
end

function CenterIslandInTile(islandID)
	bboxTable = ZomGet("Lib.Mesh.Islands." .. islandID .. ".BBoxUV")
	bbox = {}
	for i,line in ipairs(bboxTable) do
		bbox[i] = line
	end
	cu = (bbox[2]+bbox[1])/2
	cv = (bbox[4]+bbox[3])/2
	ZomDeform({PrimType="Island", WorkingSet="Visible", IDs={islandID}, Transform={ 1, 0, 0.5-cu, 0, 1, 0.5-cv, 0, 0, 1}})
end

islandIDs = IslandsSelectedByEdges()


ZomIslandCopy({Mode="EdgeSelection", WorkingSet="Visible", Orientation=ZomGet("Prefs.Similar.Mode"), AreaThreshold=ZomGet("Prefs.Similar.AreaSimilarity")})
islandIDs = IslandsSelectedByEdges()

for k,v in pairs(islandIDs) do
	CenterIslandInTile(v)
end

ZomIslandGroups({Mode="DefineGroup", WorkingSet="Visible", MergingPolicy=8322, IslandIDs=islandIDs, GroupPath="RootGroup.Children." .. groupName, AutoDelete=true})
ZomCut({PrimType="Edge", WorkingSet="Visible"})
islandIDsAfterCut = ZomGet("Lib.Mesh.RootGroup.Children.Tile_0_0.Children." .. groupName .. ".IslandIDs")

for k,v in pairs(islandIDsAfterCut) do
	CenterIslandInTile(v)
end

ZomSelect({PrimType="Island", WorkingSet="Visible", Select=true, XYZSpace=true, IDs=islandIDsAfterCut, List=true})
ZomUnfold({PrimType="Island", WorkingSet="Visible&Selected", MinAngle=1e-05, Mix=1, Iterations=ZomGet("Prefs.UnfoldIte"), PreIterations=5, StopIfOutOFDomain=false, RoomSpace=0, PinMapName="Pin", BorderIntersections=ZomGet("Prefs.OverlapsOn")})
ZomPack({ProcessTileSelection=false, RecursionDepth=1, RootGroup="RootGroup", WorkingSet="Visible&Selected", Scaling={Mode=0}, Rotate={}, Translate=true, LayoutScalingMode=0})
ZomSelect({PrimType="Island", WorkingSet="Visible", ResetBefore=true, Select=true, XYZSpace=true})

